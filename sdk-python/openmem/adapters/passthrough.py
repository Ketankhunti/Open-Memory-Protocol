"""PassthroughAdapter — native OMP HTTP forwarding (M2 / US2).

Implements the verb→HTTP mapping defined in
[contracts/passthrough-http.md](../../../specs/002-m2-pool-passthrough-adapters/contracts/passthrough-http.md).

Design notes:
- A single persistent ``httpx.Client`` is held on the instance for
  connection reuse (FR-007). Tests inject ``transport=MockTransport(...)``
  to avoid network I/O.
- Every verb method calls ``self._check_verb(verb)`` first; this enforces
  the capability gate BEFORE any network call (FR-009, EC-003).
- Error decoding lives in `_http.decode_omp_error` so `PassthroughAdapter`
  and the Supermemory translation adapter share one source of truth.
- The `Authorization: Bearer ...` header is set on the client at
  construction time and never appears in log records (FR-011).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from urllib.parse import quote

import httpx

from ..errors import (
    InvalidRequestError,
    ProviderError,
    UnsupportedCapabilityError,
)
from ..types import (
    AuditEntry,
    Capabilities,
    ContextBlock,
    Memory,
    MemoryInput,
    MemoryPage,
    MemoryUpdate,
    SearchResult,
)
from ._http import decode_omp_error, follow_one_redirect, make_client
from .base import BaseAdapter


class PassthroughAdapter(BaseAdapter):
    """Forward every OMP verb to a native OMP HTTP endpoint."""

    def __init__(
        self,
        base_url: str,
        api_key: str | None = None,
        capabilities: Capabilities | None = None,
        *,
        transport: httpx.BaseTransport | None = None,
        timeout: float = 30.0,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._capabilities = capabilities
        self._client = make_client(
            self._base_url,
            api_key,
            transport=transport,
            timeout=timeout,
        )

    # ------------------------------------------------------------- helpers

    def _request(
        self,
        method: str,
        path: str,
        *,
        json: Any = None,
        params: dict[str, Any] | None = None,
    ) -> httpx.Response:
        """Issue an HTTP request, following at most one redirect."""
        try:
            resp = self._client.request(method, path, json=json, params=params)
        except (httpx.TimeoutException, httpx.ConnectError) as exc:
            raise ProviderError(str(exc), provider="passthrough") from exc
        return follow_one_redirect(self._client, resp)

    def _parse(
        self,
        resp: httpx.Response,
        model: type[Any] | None = None,
    ) -> Any:
        """Validate ``resp`` against ``model`` or raise the right error."""
        if 200 <= resp.status_code < 300:
            if resp.status_code == 204:
                return None
            text = resp.text
            if not text:
                raise ProviderError("empty response", provider="passthrough")
            payload = resp.json()
            if model is None:
                return payload
            if isinstance(payload, list):
                return [model.model_validate(item) for item in payload]
            return model.model_validate(payload)
        raise decode_omp_error(resp, provider="passthrough")

    def _check_verb(self, verb: str) -> None:
        """Refuse pre-flight if the remote did not advertise ``verb``.

        Per FR-009 / EC-003 this MUST run before any network call so that
        capability drift cannot be silently masked by a 501 response.
        """
        caps = self.capabilities()
        if verb not in caps.verbs:
            raise UnsupportedCapabilityError(
                f"verb {verb!r} not advertised by remote (advertised: "
                f"{sorted(caps.verbs)})",
                provider="passthrough",
            )

    @staticmethod
    def _dump(model: Any) -> dict[str, Any]:
        return model.model_dump(mode="json", exclude_none=True)

    @staticmethod
    def _qs_dt(value: datetime | None) -> str | None:
        return value.isoformat() if value is not None else None

    # ------------------------------------------------------------ lifecycle

    def close(self) -> None:
        """Close the underlying HTTP client. Idempotent."""
        client = getattr(self, "_client", None)
        if client is not None:
            client.close()

    # --------------------------------------------------------------- probe

    @classmethod
    def _probe(
        cls,
        base_url: str,
        api_key: str | None = None,
        *,
        transport: httpx.BaseTransport | None = None,
    ) -> Capabilities | None:
        """Return parsed Capabilities if the endpoint speaks OMP, else None."""
        client = make_client(base_url, api_key, transport=transport, timeout=5.0)
        try:
            resp = client.get("/capabilities")
            if not (200 <= resp.status_code < 300):
                return None
            payload = resp.json()
        except Exception:
            return None
        finally:
            client.close()
        if not isinstance(payload, dict) or "omp_version" not in payload:
            return None
        try:
            return Capabilities(**payload)
        except Exception:
            return None

    # --------------------------------------------------- adapter interface

    def capabilities(self) -> Capabilities:
        """Return cached capabilities, probing on first access if needed."""
        if self._capabilities is not None:
            return self._capabilities
        # Probe via the live client so a MockTransport is honored in tests.
        try:
            resp = self._client.get("/capabilities")
        except (httpx.TimeoutException, httpx.ConnectError) as exc:
            raise UnsupportedCapabilityError(
                f"capabilities probe failed: {exc}",
                provider="passthrough",
            ) from exc
        if not (200 <= resp.status_code < 300):
            raise UnsupportedCapabilityError(
                f"endpoint {self._base_url} did not return OMP capabilities "
                f"(HTTP {resp.status_code})",
                provider="passthrough",
            )
        try:
            payload = resp.json()
            self._capabilities = Capabilities.model_validate(payload)
        except Exception as exc:
            raise UnsupportedCapabilityError(
                f"endpoint {self._base_url} returned malformed capabilities",
                provider="passthrough",
            ) from exc
        return self._capabilities

    # ------------------------------------------------------------ verbs

    def add(self, memory: MemoryInput) -> Memory:
        self._check_verb("add")
        resp = self._request("POST", "/memories", json=self._dump(memory))
        return self._parse(resp, Memory)

    def get(self, id: str) -> Memory:
        self._check_verb("get")
        resp = self._request("GET", f"/memories/{quote(id, safe='')}")
        return self._parse(resp, Memory)

    def update(self, id: str, update: MemoryUpdate) -> Memory:
        self._check_verb("update")
        resp = self._request(
            "PATCH",
            f"/memories/{quote(id, safe='')}",
            json=self._dump(update),
        )
        return self._parse(resp, Memory)

    def delete(self, id: str) -> None:
        self._check_verb("delete")
        resp = self._request("DELETE", f"/memories/{quote(id, safe='')}")
        self._parse(resp)
        return None

    def list(  # noqa: A003 — match OMP verb name
        self,
        user_id: str,
        *,
        scope: str | None = None,
        tag: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        limit: int = 50,
        cursor: str | None = None,
    ) -> MemoryPage:
        self._check_verb("list")
        params: dict[str, Any] = {"user_id": user_id, "limit": limit}
        if scope is not None:
            params["scope"] = scope
        if tag is not None:
            params["tag"] = tag
        if since is not None:
            params["since"] = self._qs_dt(since)
        if until is not None:
            params["until"] = self._qs_dt(until)
        if cursor is not None:
            # Boundary sanity check (M2.1 §2a): reject oversized or
            # non-string cursors BEFORE issuing the HTTP call so a
            # crafted cursor cannot be smuggled to the upstream server.
            if not isinstance(cursor, str) or len(cursor) > 256:
                raise InvalidRequestError(
                    "malformed cursor", provider="passthrough"
                )
            params["cursor"] = cursor
        resp = self._request("GET", "/memories", params=params)
        return self._parse(resp, MemoryPage)

    def search(
        self,
        query: str,
        user_id: str,
        *,
        scope: str | None = None,
        limit: int = 10,
        min_score: float | None = None,
    ) -> list[SearchResult]:
        self._check_verb("search")
        body: dict[str, Any] = {
            "query": query,
            "user_id": user_id,
            "limit": limit,
        }
        if scope is not None:
            body["scope"] = scope
        if min_score is not None:
            body["min_score"] = min_score
        resp = self._request("POST", "/memories/search", json=body)
        return self._parse(resp, SearchResult)

    def context(
        self,
        query: str,
        user_id: str,
        *,
        scope: str | None = None,
        token_budget: int = 500,
    ) -> ContextBlock:
        self._check_verb("context")
        body: dict[str, Any] = {
            "query": query,
            "user_id": user_id,
            "token_budget": token_budget,
        }
        if scope is not None:
            body["scope"] = scope
        resp = self._request("POST", "/context", json=body)
        return self._parse(resp, ContextBlock)

    def audit(
        self,
        user_id: str,
        *,
        app: str | None = None,
        since: datetime | None = None,
        limit: int = 100,
    ) -> list[AuditEntry]:
        self._check_verb("audit")
        params: dict[str, Any] = {"user_id": user_id, "limit": limit}
        if app is not None:
            params["app"] = app
        if since is not None:
            params["since"] = self._qs_dt(since)
        resp = self._request("GET", "/audit", params=params)
        return self._parse(resp, AuditEntry)


__all__ = ["PassthroughAdapter"]
