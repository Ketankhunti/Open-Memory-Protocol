"""SupermemoryAdapter — REST translation per contracts/supermemory-mapping.md.

Wire choice: direct REST via shared `httpx.Client` (no official Python
SDK at M2 cut). `update` is not supported by the provider and is not
advertised; calling it raises `UnsupportedCapabilityError` (EC-003).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from urllib.parse import quote

import httpx

from ..errors import (
    InvalidRequestError,
    NotFoundError,
    ProviderError,
    RateLimitedError,
    ScopeDeniedError,
    UnauthorizedError,
    UnsupportedCapabilityError,
)
from ..types import (
    AuditEntry,
    Capabilities,
    CapabilityFeatures,
    CapabilityLimits,
    ContextBlock,
    Memory,
    MemoryInput,
    MemoryPage,
    MemorySource,
    MemoryUpdate,
    SearchResult,
    _Citation,
)
from ._http import follow_one_redirect, make_client
from .base import BaseAdapter


class SupermemoryAdapter(BaseAdapter):
    """Translate OMP verbs to the Supermemory REST API."""

    def __init__(
        self,
        api_key: str,
        base_url: str = "https://api.supermemory.ai/v1",
        *,
        transport: httpx.BaseTransport | None = None,
        timeout: float = 30.0,
    ) -> None:
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._client = make_client(
            self._base_url,
            api_key,
            transport=transport,
            timeout=timeout,
        )

    def close(self) -> None:
        client = getattr(self, "_client", None)
        if client is not None:
            client.close()

    # ----------------------------------------------------- capabilities

    _CAPS = Capabilities(
        provider="supermemory",
        omp_version="0.1",
        verbs=["add", "get", "delete", "list", "search", "context"],
        features=CapabilityFeatures(
            vector_search=True,
            keyword_search=True,
            temporal=False,
            scopes="tags",
            supports_supersession=False,
            supports_audit=False,
            max_content_length=10000,
        ),
        limits=CapabilityLimits(),
    )

    def capabilities(self) -> Capabilities:
        return self._CAPS

    def _check_verb(self, verb: str) -> None:
        if verb not in self._CAPS.verbs:
            raise UnsupportedCapabilityError(
                f"verb {verb!r} not supported by supermemory",
                provider="supermemory",
            )

    # ----------------------------------------------------- HTTP

    def _request(
        self,
        method: str,
        path: str,
        *,
        json: Any = None,
        params: dict[str, Any] | None = None,
    ) -> httpx.Response:
        try:
            resp = self._client.request(method, path, json=json, params=params)
        except (httpx.TimeoutException, httpx.ConnectError) as exc:
            raise ProviderError(str(exc), provider="supermemory") from exc
        return follow_one_redirect(self._client, resp)

    def _check(self, resp: httpx.Response) -> None:
        if 200 <= resp.status_code < 300:
            return
        snippet = (resp.text or "")[:200]
        code = resp.status_code
        klass: type[ProviderError] | type[InvalidRequestError]
        if code == 401:
            klass = UnauthorizedError  # type: ignore[assignment]
        elif code == 403:
            klass = ScopeDeniedError  # type: ignore[assignment]
        elif code == 404:
            klass = NotFoundError  # type: ignore[assignment]
        elif code in (400, 422):
            klass = InvalidRequestError
        elif code == 429:
            klass = RateLimitedError  # type: ignore[assignment]
        else:
            klass = ProviderError
        raise klass(f"HTTP {code}: {snippet}", provider="supermemory")

    # ----------------------------------------------------- mapping

    @staticmethod
    def _build_metadata(inp: MemoryInput) -> dict[str, Any]:
        meta: dict[str, Any] = {}
        if inp.scope is not None:
            meta["scope"] = inp.scope
        if inp.tags:
            meta["tags"] = inp.tags
        if inp.source is not None:
            meta["source"] = inp.source.model_dump(
                mode="json", exclude_none=True
            )
        extras = getattr(inp, "model_extra", None) or {}
        for k, v in extras.items():
            if k.startswith("x-"):
                meta[k] = v
        return meta

    @staticmethod
    def _from_provider(record: dict[str, Any]) -> Memory:
        meta = record.get("metadata") or {}
        created = record.get("created_at")
        if isinstance(created, str):
            created = datetime.fromisoformat(created.replace("Z", "+00:00"))
        elif created is None:
            from datetime import timezone as _tz

            created = datetime.now(_tz.utc)
        source_dict = meta.get("source")
        source = (
            MemorySource(**source_dict) if isinstance(source_dict, dict) else None
        )
        kwargs: dict[str, Any] = {
            "id": record["id"],
            "content": record["content"],
            "user_id": record.get("user_id", ""),
            "created_at": created,
            "scope": meta.get("scope"),
            "tags": meta.get("tags"),
            "source": source,
        }
        for k, v in meta.items():
            if k.startswith("x-"):
                kwargs[k] = v
        kwargs.setdefault("x-supermemory", record)
        return Memory.model_validate(kwargs)

    # ----------------------------------------------------- verbs

    def add(self, memory: MemoryInput) -> Memory:
        self._check_verb("add")
        body: dict[str, Any] = {
            "content": memory.content,
            "user_id": memory.user_id,
        }
        meta = self._build_metadata(memory)
        if meta:
            body["metadata"] = meta
        resp = self._request("POST", "/memories", json=body)
        self._check(resp)
        return self._from_provider(resp.json())

    def get(self, id: str) -> Memory:
        self._check_verb("get")
        resp = self._request("GET", f"/memories/{quote(id, safe='')}")
        self._check(resp)
        return self._from_provider(resp.json())

    def update(self, id: str, update: MemoryUpdate) -> Memory:
        # Not advertised; fail fast.
        self._check_verb("update")  # always raises
        raise AssertionError("unreachable")  # pragma: no cover

    def delete(self, id: str) -> None:
        self._check_verb("delete")
        resp = self._request("DELETE", f"/memories/{quote(id, safe='')}")
        self._check(resp)

    def list(  # noqa: A003
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
        if cursor is not None:
            params["cursor"] = cursor
        resp = self._request("GET", "/memories", params=params)
        self._check(resp)
        payload = resp.json()
        items = [self._from_provider(r) for r in payload.get("items", [])]
        if scope is not None:
            from fnmatch import fnmatchcase
            items = [m for m in items if m.scope and fnmatchcase(m.scope, scope)]
        if tag is not None:
            items = [m for m in items if m.tags and tag in m.tags]
        return MemoryPage(items=items, next_cursor=payload.get("next_cursor"))

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
        if min_score is not None:
            body["threshold"] = min_score
        resp = self._request("POST", "/memories/search", json=body)
        self._check(resp)
        out: list[SearchResult] = []
        for item in resp.json():
            mem = self._from_provider(item)
            if scope is not None and mem.scope != scope:
                continue
            out.append(
                SearchResult(memory=mem, score=float(item.get("score", 0.0)))
            )
        return out

    def context(
        self,
        query: str,
        user_id: str,
        *,
        scope: str | None = None,
        token_budget: int = 500,
    ) -> ContextBlock:
        self._check_verb("context")
        results = self.search(query, user_id, scope=scope, limit=10)
        chunks: list[str] = []
        citations: list[_Citation] = []
        running = 0
        for r in results:
            piece = r.memory.content
            est = max(1, len(piece) // 4)
            if running + est > token_budget and chunks:
                break
            chunks.append(piece)
            citations.append(_Citation(memory_id=r.memory.id, score=r.score))
            running += est
        return ContextBlock(
            text="\n".join(chunks), citations=citations, token_count=running
        )

    def audit(
        self,
        user_id: str,
        *,
        app: str | None = None,
        since: datetime | None = None,
        limit: int = 100,
    ) -> list[AuditEntry]:
        raise UnsupportedCapabilityError(
            "audit is not supported by supermemory", provider="supermemory"
        )


__all__ = ["SupermemoryAdapter"]
