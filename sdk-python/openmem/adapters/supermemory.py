"""SupermemoryAdapter — REST translation per contracts/supermemory-mapping.md (M2.1).

Wire choice: direct REST via shared `httpx.Client` (no official Python SDK).

M2.1 changes vs M2:
- Default `base_url` is `https://api.supermemory.ai/v3` (was `/v1`); the
  `SUPERMEMORY_BASE_URL` env var overrides per FR-106.
- `add()` returns the queued-doc shape `{id, status:"queued"}` and surfaces
  it as `Memory(status="queued", content=ORIGINAL)`.
- `get()` polls `GET /memories/{id}` via `_ingest.poll_until` with budget
  `OMP_INGEST_TIMEOUT`; on timeout raises
  `ProviderError(code="ingestion_timeout", provider="supermemory")`.
- `list()` posts `POST /memories/list` with `{limit, page, filters:{user_id}}`
  and decodes camelCase pagination.
- `search()` posts `POST /search` with `{q, limit, filters:{user_id}}` and
  decodes the chunk-shaped response.
- `Memory.user_id` ALWAYS reads from `metadata.user_id` (never top-level
  `userId` — that one is provider-assigned and opaque to OMP).
- Cursor decoding uses the strict `_cursor` codec; malformed cursors raise
  `InvalidRequestError` BEFORE any HTTP call (cursor-injection defence).
- Empty `user_id` rejected BEFORE any HTTP call (cross-user broadening defence).
- `update` is not advertised; calling raises `UnsupportedCapabilityError`
  BEFORE any HTTP call (FR-009 / FR-111).
"""

from __future__ import annotations

import os
import time as _time
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote

import httpx

from ..errors import (
    InvalidRequestError,
    NotFoundError,
    OMPError,
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
from . import _cursor, _ingest
from ._http import follow_one_redirect, make_client
from .base import BaseAdapter

DEFAULT_BASE_URL = "https://api.supermemory.ai/v3"


class SupermemoryAdapter(BaseAdapter):
    """Translate OMP verbs to the Supermemory REST API."""

    def __init__(
        self,
        api_key: str,
        base_url: str | None = None,
        *,
        transport: httpx.BaseTransport | None = None,
        timeout: float = 30.0,
        block_on_add: bool | None = None,
    ) -> None:
        # api_key is private; never logged or surfaced (FR-118 / SC-107).
        self._api_key = api_key
        # M2.1: opt-in synchronous-add semantics. When True (or env
        # OMP_INGEST_BLOCK=1), add() blocks until ingestion completes and
        # returns the materialised Memory rather than a queued stub.
        self._block_on_add_flag = block_on_add
        env_base = (os.environ.get("SUPERMEMORY_BASE_URL") or "").strip()
        resolved_base = base_url if base_url is not None else (env_base or DEFAULT_BASE_URL)
        self._base_url = resolved_base.rstrip("/")
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
        features=CapabilityFeatures.model_validate(
            {
                "vector_search": True,
                "keyword_search": True,
                "temporal": False,
                "scopes": "tags",
                "supports_supersession": False,
                "supports_audit": False,
                "max_content_length": 10000,
                "status_field": True,
                "async_ingestion": True,
            }
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
        klass: type[OMPError]
        if code == 401:
            klass = UnauthorizedError
        elif code == 403:
            klass = ScopeDeniedError
        elif code == 404:
            klass = NotFoundError
        elif code in (400, 422):
            klass = InvalidRequestError
        elif code == 429:
            klass = RateLimitedError
        else:
            klass = ProviderError
        raise klass(f"HTTP {code}: {snippet}", provider="supermemory")

    # ----------------------------------------------------- mapping

    @staticmethod
    def _build_metadata(
        inp: MemoryInput, *, include_user_id: str | None = None
    ) -> dict[str, Any]:
        meta: dict[str, Any] = {}
        if include_user_id is not None:
            meta["user_id"] = include_user_id
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
    def _from_provider(record: dict[str, Any], *, status: str | None = None) -> Memory:
        meta = record.get("metadata") or {}
        # M2.1 invariant: Memory.user_id is read from metadata.user_id, NEVER
        # from top-level `userId` (which is provider-assigned).
        user_id = meta.get("user_id") or record.get("user_id") or ""
        # Accept either snake_case (mock-mode shim) or camelCase (live API).
        created_raw = (
            record.get("created_at")
            or record.get("createdAt")
            or None
        )
        if isinstance(created_raw, str):
            created = datetime.fromisoformat(created_raw.replace("Z", "+00:00"))
        elif created_raw is None:
            created = datetime.now(timezone.utc)
        else:
            created = created_raw
        source_dict = meta.get("source")
        source = (
            MemorySource(**source_dict) if isinstance(source_dict, dict) else None
        )
        kwargs: dict[str, Any] = {
            "id": record["id"],
            "content": record.get("content") or record.get("title") or "",
            "user_id": user_id,
            "created_at": created,
            "scope": meta.get("scope"),
            "tags": meta.get("tags"),
            "source": source,
        }
        if status is not None:
            kwargs["status"] = status
        upstream_status = record.get("status")
        if status is None and isinstance(upstream_status, str):
            # Map upstream "queued"/"indexing"/"done"/"failed" through verbatim
            # when it matches the OMP enum.
            valid = {"queued", "indexing", "done", "failed"}
            if upstream_status.lower() in valid:
                kwargs["status"] = upstream_status.lower()
        for k, v in meta.items():
            if k.startswith("x-"):
                kwargs[k] = v
        kwargs.setdefault(
            "x-supermemory",
            {"upstream_status": record.get("status")} if "status" in record else {"raw": True},
        )
        return Memory.model_validate(kwargs)

    # ----------------------------------------------------- verbs

    def add(self, memory: MemoryInput) -> Memory:
        self._check_verb("add")
        body: dict[str, Any] = {
            "content": memory.content,
            "metadata": self._build_metadata(
                memory, include_user_id=memory.user_id
            ),
        }
        resp = self._request("POST", "/documents", json=body)
        self._check(resp)
        payload = resp.json()
        # Live shape: {id, status:"queued"} (no full record yet).
        if isinstance(payload, dict) and set(payload.keys()) <= {"id", "status", "message"}:
            queued = Memory.model_validate(
                {
                    "id": payload["id"],
                    "content": memory.content,
                    "user_id": memory.user_id,
                    "created_at": datetime.now(timezone.utc),
                    "scope": memory.scope,
                    "tags": memory.tags,
                    "status": "queued",
                    "x-supermemory": {"upstream_status": payload.get("status")},
                }
            )
            # M2.1: opt-in synchronous-add semantics. Blocks for up to
            # OMP_INGEST_TIMEOUT seconds while supermemory transitions the
            # doc through queued → chunking → indexing → done. Returns
            # the materialised Memory; on timeout surfaces the queued stub
            # so callers retain the upstream id and can poll later.
            if self._should_block_on_add():
                try:
                    return self.get(payload["id"])
                except ProviderError as exc:
                    if getattr(exc, "code", None) != "ingestion_timeout":
                        raise
                    return queued
            return queued
        # Mock-mode / legacy shape: full record (back-compat).
        return self._from_provider(payload)

    def _should_block_on_add(self) -> bool:
        """Return True if add() should block until ingestion completes.

        Honours the constructor flag, then OMP_INGEST_BLOCK=1.
        """
        if self._block_on_add_flag is not None:
            return bool(self._block_on_add_flag)
        return (os.environ.get("OMP_INGEST_BLOCK") or "").strip() == "1"

    def get(self, id: str) -> Memory:
        self._check_verb("get")
        if not id or not isinstance(id, str):
            raise InvalidRequestError("id is required", provider="supermemory")
        timeout = float(_ingest.read_ingest_timeout_env())

        def _try_fetch() -> dict[str, Any] | None:
            resp = self._request("GET", f"/documents/{quote(id, safe='')}")
            if resp.status_code == 404:
                return None
            # M2.1: supermemory returns 409 "Document is still processing"
            # while async ingestion is in progress; treat as not-yet-ready
            # and let poll_until retry within the OMP_INGEST_TIMEOUT budget.
            if resp.status_code == 409:
                return None
            try:
                self._check(resp)
            except NotFoundError:
                return None
            payload = resp.json()
            # M2.1: supermemory may return a 200 envelope with
            # `status="deleted"` for tombstoned docs; treat as not found
            # so the contract delete-then-get behaviour is honoured.
            upstream_status = (payload.get("status") or "").lower()
            if upstream_status in {"deleted", "removed", "tombstone"}:
                return None
            return payload

        record = _ingest.poll_until(
            _try_fetch,
            timeout=timeout,
            provider="supermemory",
            on_timeout_details={"id": id},
        )
        return self._from_provider(record, status="done")

    def wait_for_ingest(
        self,
        ids: list[str],
        user_id: str,
        *,
        timeout: float | None = None,
    ) -> None:
        """Wait until all ``ids`` reach a terminal (non-queued/indexing) state.

        Issues one GET per pending id per cycle and removes any id that has
        materialised. Treats 409 (still-processing) and 404 (not-yet-visible)
        as still-pending. Pending sets shrink across cycles, keeping wall
        clock close to the slowest single ingest rather than O(N) per id.
        """
        if not ids:
            return
        budget = float(timeout) if timeout is not None else float(_ingest.read_ingest_timeout_env())
        pending = set(ids)

        def _try_resolve_all() -> bool | None:
            done: set[str] = set()
            for doc_id in pending:
                resp = self._request("GET", f"/documents/{quote(doc_id, safe='')}")
                if resp.status_code in (404, 409):
                    continue  # still pending
                try:
                    self._check(resp)
                except NotFoundError:
                    continue
                payload = resp.json()
                upstream_status = (payload.get("status") or "").lower()
                if upstream_status in {"queued", "indexing", "processing"}:
                    continue
                done.add(doc_id)
            pending.difference_update(done)
            return True if not pending else None

        _ingest.poll_until(
            _try_resolve_all,
            timeout=budget,
            provider="supermemory",
            on_timeout_details={"phase": "wait_for_ingest", "ids": list(pending)},
        )

    def update(self, id: str, update: MemoryUpdate) -> Memory:
        # Pre-flight: not advertised → raise BEFORE any HTTP call.
        self._check_verb("update")  # always raises
        raise AssertionError("unreachable")  # pragma: no cover

    def delete(self, id: str) -> None:
        self._check_verb("delete")
        if not id or not isinstance(id, str):
            raise InvalidRequestError("id is required", provider="supermemory")
        # M2.1: supermemory may return 409 "Document is still processing"
        # if the doc has not yet finished its queued → indexing transition.
        # Block briefly until ingestion completes, then DELETE; this lets
        # cleanup hooks succeed without leaking docs.
        timeout = float(_ingest.read_ingest_timeout_env())
        deadline = _time.monotonic() + timeout
        while True:
            resp = self._request("DELETE", f"/documents/{quote(id, safe='')}")
            if resp.status_code in (204, 404):
                return  # idempotent
            if resp.status_code == 409 and _time.monotonic() < deadline:
                _time.sleep(min(2.0, max(0.5, timeout / 20)))
                continue
            self._check(resp)
            return

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
        # Cursor validation runs BEFORE HTTP call (defence against injection
        # / quota-exhaustion via crafted cursors).
        page = _cursor.decode_cursor(cursor)
        body: dict[str, Any] = {
            "limit": limit,
            "page": page,
        }
        # Live API requires filters as `{AND: [{key, value}]}` — flat
        # `{user_id: ...}` is rejected with HTTP 400.
        if user_id:
            body["filters"] = {
                "AND": [{"key": "user_id", "value": user_id}]
            }
        resp = self._request("POST", "/documents/list", json=body)
        self._check(resp)
        payload = resp.json()
        # camelCase live shape:
        #   {memories:[...], pagination:{currentPage, limit, totalPages, ...}}
        # Mock-mode legacy shape: {items:[...], next_cursor:"..."}
        if "memories" in payload:
            raw_items = payload.get("memories") or []
            pagination = payload.get("pagination") or {}
            current = int(pagination.get("currentPage", page))
            total_pages = int(pagination.get("totalPages", current))
            has_next = current < total_pages
        else:
            raw_items = payload.get("items") or []
            has_next = (
                payload.get("next_cursor") is not None
                or len(raw_items) >= limit
            )

        items = [self._from_provider(r, status="done") for r in raw_items]
        if scope is not None:
            from fnmatch import fnmatchcase

            items = [m for m in items if m.scope and fnmatchcase(m.scope, scope)]
        if tag is not None:
            items = [m for m in items if m.tags and tag in m.tags]
        next_cursor = _cursor.encode_cursor(page + 1) if has_next else None
        return MemoryPage(items=items, next_cursor=next_cursor)

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
        # Pre-flight: empty user_id MUST raise BEFORE any upstream call.
        if not user_id or not str(user_id).strip():
            raise InvalidRequestError("user_id is required", provider="supermemory")
        body: dict[str, Any] = {
            "q": query,
            "limit": limit,
            # Live API filter shape (matches list).
            "filters": {"AND": [{"key": "user_id", "value": user_id}]},
        }
        if min_score is not None:
            body["threshold"] = min_score
        resp = self._request("POST", "/search", json=body)
        self._check(resp)
        payload = resp.json()
        out: list[SearchResult] = []

        # Live shape: {results:[{documentId, score, title, chunks:[...]}], ...}
        if isinstance(payload, dict) and "results" in payload:
            for hit in payload.get("results") or []:
                doc_id = hit.get("documentId") or hit.get("id")
                if doc_id is None:
                    continue
                chunks = hit.get("chunks") or []
                best_chunk_score = (
                    max((float(c.get("score", 0.0)) for c in chunks), default=0.0)
                    if chunks
                    else 0.0
                )
                score = float(hit.get("score") or best_chunk_score)
                if min_score is not None and score < min_score:
                    continue
                content = hit.get("title") or (
                    chunks[0].get("content") if chunks else ""
                )
                meta = hit.get("metadata") or {}
                synthetic = {
                    "id": doc_id,
                    "content": content,
                    "user_id": meta.get("user_id") or user_id,
                    "created_at": hit.get("createdAt") or hit.get("created_at"),
                    "metadata": meta,
                    "status": "done",
                }
                mem = self._from_provider(synthetic, status="done")
                if scope is not None and mem.scope != scope:
                    continue
                out.append(SearchResult(memory=mem, score=score))
            return out

        # Mock-mode / legacy shape: list of full records with `score`.
        for item in payload if isinstance(payload, list) else []:
            mem = self._from_provider(item, status="done")
            score = float(item.get("score", 0.0))
            if min_score is not None and score < min_score:
                continue
            if scope is not None and mem.scope != scope:
                continue
            out.append(SearchResult(memory=mem, score=score))
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
