"""Async passthrough adapter (T016 / M3.2).

Native async mirror of :class:`openmem.adapters.passthrough.PassthroughAdapter`,
implemented on top of ``httpx.AsyncClient``.

Cancellation contract (contracts/async-memory.md §3, *Native* tier):

* Every verb is a single ``await self._client.request(...)`` call —
  cancelling the awaiter aborts the in-flight request and httpx releases
  the underlying socket back to the connection pool within 500 ms (C-CAN-2).
* No ``timeout=None`` is used; the constructor's timeout governs every
  request. No ``try/except asyncio.CancelledError`` is anywhere in this
  module (cancel must propagate cleanly).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from urllib.parse import quote

import httpx

from ..errors import (
    InvalidRequestError,
    OMPError,
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
from ._http import decode_omp_error
from ._validation import require_user_id

__all__ = ["AsyncPassthroughAdapter"]


def _make_async_client(
    base_url: str,
    api_key: str | None,
    *,
    transport: httpx.AsyncBaseTransport | httpx.MockTransport | None,
    timeout: float,
) -> httpx.AsyncClient:
    headers: dict[str, str] = {
        "Accept": "application/json",
        "User-Agent": "openmem-python/0.4.0-async",
    }
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    return httpx.AsyncClient(
        base_url=base_url.rstrip("/"),
        headers=headers,
        timeout=timeout,
        transport=transport,
        follow_redirects=False,
    )


class AsyncPassthroughAdapter:
    """Forward every async OMP verb to a native OMP HTTP endpoint."""

    def __init__(
        self,
        base_url: str,
        api_key: str | None = None,
        capabilities: Capabilities | None = None,
        *,
        transport: httpx.AsyncBaseTransport | httpx.MockTransport | None = None,
        timeout: float = 30.0,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._capabilities = capabilities
        self._transport = transport
        self._timeout = timeout
        self._client: httpx.AsyncClient | None = None

    # --------------------------------------------------------- lifecycle

    def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = _make_async_client(
                self._base_url,
                self._api_key,
                transport=self._transport,
                timeout=self._timeout,
            )
        return self._client

    async def close(self) -> None:
        client = self._client
        if client is not None:
            self._client = None
            try:
                await client.aclose()
            except Exception:
                pass

    # ------------------------------------------------------------ helpers

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json: Any = None,
        params: dict[str, Any] | None = None,
    ) -> httpx.Response:
        client = self._ensure_client()
        try:
            resp = await client.request(method, path, json=json, params=params)
        except (httpx.TimeoutException, httpx.ConnectError) as exc:
            raise ProviderError(str(exc), provider="passthrough") from exc
        # One-redirect rule (mirrors sync passthrough).
        if 300 <= resp.status_code < 400:
            location = resp.headers.get("location")
            if not location:
                raise ProviderError(
                    f"HTTP {resp.status_code} redirect without Location header"
                )
            try:
                follow = await client.request(method, location)
            except (httpx.TimeoutException, httpx.ConnectError) as exc:
                raise ProviderError(str(exc), provider="passthrough") from exc
            if 300 <= follow.status_code < 400:
                raise ProviderError("redirect loop")
            return follow
        return resp

    def _parse(
        self,
        resp: httpx.Response,
        model: type[Any] | None = None,
    ) -> Any:
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

    async def _check_verb(self, verb: str) -> None:
        caps = await self.capabilities()
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

    # ------------------------------------------------------------ verbs

    async def capabilities(self) -> Capabilities:
        if self._capabilities is not None:
            return self._capabilities
        client = self._ensure_client()
        try:
            resp = await client.get("/capabilities")
        except (httpx.TimeoutException, httpx.ConnectError) as exc:
            raise UnsupportedCapabilityError(
                f"capabilities probe failed: {exc}", provider="passthrough"
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

    async def add(self, memory: MemoryInput) -> Memory:
        require_user_id(memory.user_id, provider="passthrough")
        await self._check_verb("add")
        resp = await self._request("POST", "/memories", json=self._dump(memory))
        return self._parse(resp, Memory)

    async def get(self, id: str) -> Memory:
        await self._check_verb("get")
        resp = await self._request("GET", f"/memories/{quote(id, safe='')}")
        return self._parse(resp, Memory)

    async def update(self, id: str, update: MemoryUpdate) -> Memory:
        await self._check_verb("update")
        resp = await self._request(
            "PATCH",
            f"/memories/{quote(id, safe='')}",
            json=self._dump(update),
        )
        return self._parse(resp, Memory)

    async def delete(self, id: str) -> None:
        await self._check_verb("delete")
        resp = await self._request("DELETE", f"/memories/{quote(id, safe='')}")
        self._parse(resp)
        return None

    async def list(  # noqa: A003
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
        require_user_id(user_id, provider="passthrough")
        await self._check_verb("list")
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
            if not isinstance(cursor, str) or len(cursor) > 256:
                raise InvalidRequestError(
                    "malformed cursor", provider="passthrough"
                )
            params["cursor"] = cursor
        resp = await self._request("GET", "/memories", params=params)
        return self._parse(resp, MemoryPage)

    async def search(
        self,
        query: str,
        user_id: str,
        *,
        scope: str | None = None,
        limit: int = 10,
        min_score: float | None = None,
    ) -> list[SearchResult]:
        require_user_id(user_id, provider="passthrough")
        await self._check_verb("search")
        body: dict[str, Any] = {
            "query": query,
            "user_id": user_id,
            "limit": limit,
        }
        if scope is not None:
            body["scope"] = scope
        if min_score is not None:
            body["min_score"] = min_score
        resp = await self._request("POST", "/memories/search", json=body)
        return self._parse(resp, SearchResult)

    async def context(
        self,
        query: str,
        user_id: str,
        *,
        scope: str | None = None,
        token_budget: int = 500,
    ) -> ContextBlock:
        require_user_id(user_id, provider="passthrough")
        await self._check_verb("context")
        body: dict[str, Any] = {
            "query": query,
            "user_id": user_id,
            "token_budget": token_budget,
        }
        if scope is not None:
            body["scope"] = scope
        resp = await self._request("POST", "/context", json=body)
        return self._parse(resp, ContextBlock)

    async def audit(
        self,
        user_id: str,
        *,
        app: str | None = None,
        since: datetime | None = None,
        limit: int = 100,
    ) -> list[AuditEntry]:
        require_user_id(user_id, provider="passthrough")
        await self._check_verb("audit")
        params: dict[str, Any] = {"user_id": user_id, "limit": limit}
        if app is not None:
            params["app"] = app
        if since is not None:
            params["since"] = self._qs_dt(since)
        resp = await self._request("GET", "/audit", params=params)
        return self._parse(resp, AuditEntry)

    async def wait_for_ingest(
        self,
        ids: list[str],
        user_id: str,
        *,
        timeout: float | None = None,
    ) -> None:
        return None
