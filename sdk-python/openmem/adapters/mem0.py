"""Mem0Adapter — translates OMP verbs to the Mem0 Python SDK (M2.1).

Mapping authority: [contracts/mem0-mapping.md](../../../specs/003-m2-1-live/contracts/mem0-mapping.md).

M2.1 changes vs M2:
- `add()` recognises the v2 async response shape `{event_id, status:"PENDING"}`
  and returns `Memory(id=event_id, status="queued", content=ORIGINAL,
  x-mem0={event_id, original_content})`.
- `get()` wraps the SDK call in `_ingest.poll_until` with budget
  `OMP_INGEST_TIMEOUT` (env, default 60); on timeout raises
  `ProviderError(code="ingestion_timeout", provider="mem0",
  details={"event_id": id, ...})`.
- `list()`/`search()` use the strict `_cursor` codec; malformed cursors
  raise `InvalidRequestError` BEFORE any upstream call.
- `search()` rejects empty `user_id` BEFORE any upstream call.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any

from ..errors import (
    InvalidRequestError,
    NotFoundError,
    OMPError,
    ProviderError,
    RateLimitedError,
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
from ._validation import require_user_id
from .base import BaseAdapter


class Mem0Adapter(BaseAdapter):
    """Translate OMP verbs to the Mem0 Python SDK."""

    def __init__(
        self,
        api_key: str,
        host: str = "https://api.mem0.ai",
        *,
        client: Any = None,
        block_on_add: bool | None = None,
    ) -> None:
        # NOTE: `api_key` is held privately and MUST NEVER appear in any
        # log statement, exception message, or repr (FR-118 / SC-107).
        self._api_key = api_key
        self._host = host
        # M2.1: opt-in synchronous-add semantics. When True (or env
        # OMP_INGEST_BLOCK=1), add() blocks until ingestion completes and
        # returns the materialised Memory rather than a queued stub.
        self._block_on_add_flag = block_on_add
        # Cache of `event_id → original_content` so that `get()` after a
        # rewrite can surface the user's original phrasing under
        # `x-mem0.original_content` even when the SDK never echoes it.
        self._original_by_event: dict[str, str] = {}
        # Cache of `event_id → user_id` and `event_id → resolved memory id`
        # used to translate v2-async event ids to materialised memory ids
        # the first time `get()` polls successfully (M2.1 live-mode).
        self._user_by_event: dict[str, str] = {}
        self._resolved_event: dict[str, str] = {}
        if client is not None:
            self._client = client
        else:
            try:
                from mem0 import MemoryClient  # type: ignore
            except ImportError as exc:  # pragma: no cover - exercised via tests
                raise ImportError(
                    "Mem0Adapter requires the `mem0ai` package; "
                    "run `pip install openmem[mem0]`"
                ) from exc
            self._client = MemoryClient(api_key=api_key, host=host)

    # ------------------------------------------------------------- mapping

    @staticmethod
    def _build_metadata(inp: MemoryInput | MemoryUpdate) -> dict[str, Any]:
        meta: dict[str, Any] = {}
        if getattr(inp, "scope", None) is not None:
            meta["scope"] = inp.scope
        if getattr(inp, "tags", None):
            meta["tags"] = inp.tags
        src = getattr(inp, "source", None)
        if src is not None:
            meta["source"] = src.model_dump(mode="json", exclude_none=True)
        for fld in ("confidence", "valid_from", "valid_to", "supersedes"):
            v = getattr(inp, fld, None)
            if v is not None:
                meta[fld] = v.isoformat() if isinstance(v, datetime) else v
        extras = getattr(inp, "model_extra", None) or {}
        for k, v in extras.items():
            if k.startswith("x-"):
                meta[k] = v
        return meta

    def _from_provider(
        self,
        record: dict[str, Any],
        *,
        status: str | None = None,
        original_content: str | None = None,
    ) -> Memory:
        meta = record.get("metadata") or {}
        created = record.get("created_at") or datetime.now(timezone.utc)
        if isinstance(created, str):
            created = datetime.fromisoformat(created.replace("Z", "+00:00"))
        source_dict = meta.get("source")
        source = MemorySource(**source_dict) if isinstance(source_dict, dict) else None
        valid_from = meta.get("valid_from")
        valid_to = meta.get("valid_to")
        if isinstance(valid_from, str):
            valid_from = datetime.fromisoformat(valid_from)
        if isinstance(valid_to, str):
            valid_to = datetime.fromisoformat(valid_to)
        # mem0 v2 may serialise list-typed metadata fields as strings
        # (single tag → "nodejs"; supersedes may also be flattened).
        # Coerce back to list to honour the OMP wire schema (M2.1).
        raw_tags = meta.get("tags")
        if isinstance(raw_tags, str):
            raw_tags = [t.strip() for t in raw_tags.split(",") if t.strip()]
        raw_supersedes = meta.get("supersedes")
        if isinstance(raw_supersedes, str):
            raw_supersedes = [
                s.strip() for s in raw_supersedes.split(",") if s.strip()
            ]
        kwargs: dict[str, Any] = {
            "id": record["id"],
            "content": record.get("memory") or record.get("content", ""),
            "user_id": record.get("user_id", meta.get("user_id", "")),
            "created_at": created,
            "updated_at": (
                datetime.fromisoformat(record["updated_at"].replace("Z", "+00:00"))
                if isinstance(record.get("updated_at"), str)
                else record.get("updated_at")
            ),
            "scope": meta.get("scope"),
            "tags": raw_tags,
            "source": source,
            "confidence": meta.get("confidence"),
            "valid_from": valid_from,
            "valid_to": valid_to,
            "supersedes": raw_supersedes,
        }
        if status is not None:
            kwargs["status"] = status
        for k, v in meta.items():
            if k.startswith("x-"):
                kwargs[k] = v
        # Resolve / inject x-mem0 extension dict carrying original content.
        # mem0 v2 may serialise dict-typed metadata as a JSON string;
        # coerce back to dict so downstream code can index into it.
        raw_x_mem0 = meta.get("x-mem0")
        if isinstance(raw_x_mem0, str):
            try:
                raw_x_mem0 = json.loads(raw_x_mem0)
            except (json.JSONDecodeError, ValueError):
                raw_x_mem0 = {}
        if not isinstance(raw_x_mem0, dict):
            raw_x_mem0 = {}
        x_mem0 = dict(raw_x_mem0)
        if original_content is not None:
            x_mem0.setdefault("original_content", original_content)
        cached = self._original_by_event.get(record["id"])
        if cached is not None and "original_content" not in x_mem0:
            x_mem0["original_content"] = cached
        if x_mem0:
            kwargs["x-mem0"] = x_mem0
        return Memory.model_validate(kwargs)

    @staticmethod
    def _translate_error(exc: BaseException) -> OMPError:
        if isinstance(exc, OMPError):
            return exc
        name = type(exc).__name__
        msg = str(exc)
        mapping = {
            "AuthenticationError": UnauthorizedError,
            "NotFoundError": NotFoundError,
            # mem0 SDK ≥ 2.0 raises MemoryNotFoundError for `get`/`delete`
            # of unknown ids (M2.1 live-mode finding).
            "MemoryNotFoundError": NotFoundError,
            "ValidationError": InvalidRequestError,
            "RateLimitError": RateLimitedError,
        }
        klass = mapping.get(name, ProviderError)
        return klass(msg, provider="mem0")

    def _should_block_on_add(self) -> bool:
        """Return True if add() should block until upstream ingestion completes.

        Honours an explicit constructor flag, then falls back to the
        process-wide env var ``OMP_INGEST_BLOCK`` (strict "1" comparison).
        """
        if self._block_on_add_flag is not None:
            return bool(self._block_on_add_flag)
        return (os.environ.get("OMP_INGEST_BLOCK") or "").strip() == "1"

    # ---------------------------------------------------- capabilities

    _CAPS = Capabilities(
        provider="mem0",
        omp_version="0.1",
        verbs=["add", "get", "update", "delete", "list", "search", "context"],
        features=CapabilityFeatures.model_validate(
            {
                "vector_search": True,
                "keyword_search": True,
                "temporal": False,
                "scopes": "tags",
                "supports_supersession": False,
                "supports_audit": False,
                "max_content_length": 10000,
                # M2.1 additions (T022) — advertised as model extras
                # because they're new flags not yet in the OpenAPI schema.
                "status_field": True,
                "async_ingestion": True,
            }
        ),
        limits=CapabilityLimits(),
    )

    def capabilities(self) -> Capabilities:
        return self._CAPS

    # ------------------------------------------------------------- verbs

    def add(self, memory: MemoryInput) -> Memory:
        meta = self._build_metadata(memory)
        original = memory.content
        try:
            result = self._client.add(
                messages=[{"role": "user", "content": original}],
                user_id=memory.user_id,
                metadata=meta,
            )
        except Exception as exc:
            raise self._translate_error(exc) from exc

        # ----- v2 async shape: {message, status: "PENDING", event_id} -----
        if isinstance(result, dict) and "event_id" in result:
            event_id = str(result["event_id"])
            self._original_by_event[event_id] = original
            self._user_by_event[event_id] = memory.user_id
            queued = Memory.model_validate(
                {
                    "id": event_id,
                    "content": original,
                    "user_id": memory.user_id,
                    "created_at": datetime.now(timezone.utc),
                    "scope": memory.scope,
                    "tags": memory.tags,
                    "status": "queued",
                    "x-mem0": {
                        "event_id": event_id,
                        "original_content": original,
                    },
                }
            )
            # M2.1: callers that opt in via OMP_INGEST_BLOCK=1 (or set the
            # `block_on_add=True` constructor flag) get a fully-materialised
            # Memory back from add(), matching the OMP synchronous-add
            # contract. Blocks for up to OMP_INGEST_TIMEOUT seconds. When
            # mem0's LLM filters trivial input as non-factual the underlying
            # record never materialises; in that case we fall back to a
            # deterministic synthetic id (status=None) so the caller can
            # detect the no-op without an exception.
            if self._should_block_on_add():
                try:
                    return self.get(event_id)
                except ProviderError as exc:
                    if getattr(exc, "code", None) != "ingestion_timeout":
                        raise
                    synthetic_id = (
                        f"mem_noop_{abs(hash(original)) & 0xFFFFFFFF:08x}"
                    )
                    return Memory.model_validate(
                        {
                            "id": synthetic_id,
                            "content": original,
                            "user_id": memory.user_id,
                            "created_at": datetime.now(timezone.utc),
                            "scope": memory.scope,
                            "tags": memory.tags,
                            "status": None,
                            "x-mem0": {
                                "event_id": event_id,
                                "original_content": original,
                                "noop": True,
                            },
                        }
                    )
            return queued

        # ----- low-information shape: {results: []} (EC-102) -----
        if (
            isinstance(result, dict)
            and "results" in result
            and not result["results"]
        ):
            synthetic_id = f"mem_noop_{abs(hash(original)) & 0xFFFFFFFF:08x}"
            return Memory.model_validate(
                {
                    "id": synthetic_id,
                    "content": original,
                    "user_id": memory.user_id,
                    "created_at": datetime.now(timezone.utc),
                    "scope": memory.scope,
                    "tags": memory.tags,
                    "status": None,
                    "x-mem0": {"original_content": original, "noop": True},
                }
            )

        # ----- legacy / mock shape: list[record] or {results:[record]} -----
        if isinstance(result, dict) and "results" in result:
            result = result["results"][0]
        elif isinstance(result, list) and result:
            result = result[0]

        if not isinstance(result, dict):
            raise ProviderError(
                f"unexpected mem0.add response type: {type(result).__name__}",
                provider="mem0",
            )
        return self._from_provider(result, original_content=original)

    def get(self, id: str) -> Memory:
        if not id or not isinstance(id, str):
            raise InvalidRequestError("id is required", provider="mem0")
        timeout = float(_ingest.read_ingest_timeout_env())
        original = self._original_by_event.get(id)
        # M2.1: mem0 v2 async returns an `event_id` from add() that is
        # NOT a memory_id — `client.get(memory_id=event_id)` will 404
        # forever. Resolve the event to the materialised memory id via
        # `client.get_all(filters={user_id})` polling, then cache.
        resolved = self._resolved_event.get(id)
        user_for_event = self._user_by_event.get(id)
        if resolved is None and user_for_event is not None:
            resolved = self._resolve_event_id(
                event_id=id,
                user_id=user_for_event,
                original=original or "",
                timeout=timeout,
            )
            self._resolved_event[id] = resolved
        lookup_id = resolved or id

        def _try_fetch() -> dict[str, Any] | None:
            try:
                rec = self._client.get(memory_id=lookup_id)
            except NotFoundError:
                return None
            except Exception as exc:
                err = self._translate_error(exc)
                if isinstance(err, NotFoundError):
                    return None
                raise err from exc
            return rec

        record = _ingest.poll_until(
            _try_fetch,
            timeout=timeout,
            provider="mem0",
            on_timeout_details={"event_id": id},
        )
        # Preserve the caller-visible id (the v2 event id) so OMP
        # consumers see stable identifiers across add() → get() (M2.1).
        if user_for_event is not None and record.get("id") != id:
            record = dict(record)
            record["id"] = id
        return self._from_provider(record, status="done", original_content=original)

    def _resolve_event_id(
        self,
        *,
        event_id: str,
        user_id: str,
        original: str,
        timeout: float,
    ) -> str:
        """Poll `get_all` until a memory matching ``original`` surfaces.

        mem0 v2 add() is asynchronous and returns an opaque ``event_id``
        rather than the materialised memory id. We discover the real id
        by listing memories for the user and matching either by exact
        content (legacy) or by substring overlap (LLM-rewritten).
        Returns the discovered memory id, or falls back to ``event_id``
        when polling times out so the caller's 404 surfaces verbatim.
        """
        probe_tokens = [
            tok for tok in (original or "").split() if len(tok) >= 4
        ]

        def _try_resolve() -> str | None:
            try:
                page = self._client.get_all(
                    version="v2",
                    filters={"user_id": user_id},
                    page_size=50,
                )
            except Exception:  # noqa: BLE001
                return None
            items: list[dict[str, Any]]
            if isinstance(page, dict):
                items = list(page.get("results") or page.get("memories") or [])
            elif isinstance(page, list):
                items = page
            else:
                items = []
            for it in items:
                rec_id = it.get("id")
                content = it.get("memory") or it.get("content") or ""
                if not rec_id:
                    continue
                if original and original in content:
                    return str(rec_id)
                if probe_tokens and any(t in content for t in probe_tokens):
                    return str(rec_id)
            # No content match — accept the most recent record as a
            # last-resort heuristic (mem0 LLM rewrites can drop every
            # token of the original).
            if items and not probe_tokens:
                rec_id = items[0].get("id")
                if rec_id:
                    return str(rec_id)
            return None

        try:
            return _ingest.poll_until(
                _try_resolve,
                timeout=timeout,
                provider="mem0",
                on_timeout_details={"event_id": event_id, "phase": "resolve"},
            )
        except ProviderError:
            return event_id

    def update(self, id: str, update: MemoryUpdate) -> Memory:
        # Translate event ids to materialised ids when known (M2.1).
        target = self._resolved_event.get(id)
        if target is None and id in self._user_by_event:
            # Force resolution by triggering a get() (which polls).
            try:
                self.get(id)
                target = self._resolved_event.get(id, id)
            except Exception:  # noqa: BLE001
                target = id
        elif target is None:
            target = id
        try:
            if update.content is not None:
                # mem0 SDK expects keyword `text=` (UpdateMemoryOptions),
                # not `data=` (M2.1 live-mode finding).
                self._client.update(memory_id=target, text=update.content)
            extras_changed = (
                update.scope is not None
                or update.tags is not None
                or update.confidence is not None
                or update.valid_to is not None
                or update.supersedes is not None
            )
            if extras_changed:
                meta = self._build_metadata(update)
                if hasattr(self._client, "update_metadata"):
                    self._client.update_metadata(memory_id=target, metadata=meta)
            record = self._client.get(memory_id=target)
        except Exception as exc:
            raise self._translate_error(exc) from exc
        return self._from_provider(record, status="done")

    def delete(self, id: str) -> None:
        # Translate event ids to materialised ids when already known.
        # We do NOT trigger a resolve poll here (that can take ~30 s per
        # call when fan-out is large, e.g. cleanup of pagination tests).
        # Callers who need delete-by-event must call get(id) first.
        candidates: list[str] = []
        resolved = self._resolved_event.get(id)
        if resolved and resolved != id:
            candidates.append(resolved)
        candidates.append(id)
        last_err: Exception | None = None
        for target in candidates:
            try:
                self._client.delete(memory_id=target)
                return
            except Exception as exc:
                err = self._translate_error(exc)
                if isinstance(err, NotFoundError):
                    last_err = err
                    continue
                raise err from exc
        # All candidates 404'd — idempotent delete contract: not an error.
        if last_err is None:
            return

    def wait_for_ingest(
        self,
        ids: list[str],
        user_id: str,
        *,
        timeout: float | None = None,
    ) -> None:
        """Batch-resolve a set of event ids in a single poll loop.

        Polls ``get_all`` once per cycle and resolves every still-pending
        event id from that single page, instead of running N independent
        per-id poll loops (M2.1 perf — turns 6×30s serial waits into one
        ~30s wait).
        """
        if not ids:
            return
        # Only event-ids managed by this adapter need resolution; ids we
        # already resolved during this process are read-after-write safe.
        pending = [
            i for i in ids
            if i in self._user_by_event and i not in self._resolved_event
        ]
        if not pending:
            return
        budget = float(timeout) if timeout is not None else float(_ingest.read_ingest_timeout_env())
        # Probe-token sets per pending id (LLM-rewrite tolerance).
        probes: dict[str, list[str]] = {}
        originals: dict[str, str] = {}
        for ev in pending:
            original = self._original_by_event.get(ev, "")
            originals[ev] = original
            probes[ev] = [tok for tok in (original or "").split() if len(tok) >= 4]

        def _try_resolve_all() -> bool | None:
            try:
                page = self._client.get_all(
                    version="v2",
                    filters={"user_id": user_id},
                    page_size=100,
                )
            except Exception:  # noqa: BLE001
                return None
            items: list[dict[str, Any]]
            if isinstance(page, dict):
                items = list(page.get("results") or page.get("memories") or [])
            elif isinstance(page, list):
                items = page
            else:
                items = []
            still_pending: list[str] = []
            for ev in pending:
                if ev in self._resolved_event:
                    continue
                original = originals[ev]
                tokens = probes[ev]
                matched: str | None = None
                for it in items:
                    rec_id = it.get("id")
                    content = it.get("memory") or it.get("content") or ""
                    if not rec_id:
                        continue
                    if original and original in content:
                        matched = str(rec_id)
                        break
                    if tokens and any(t in content for t in tokens):
                        matched = str(rec_id)
                        break
                if matched:
                    self._resolved_event[ev] = matched
                else:
                    still_pending.append(ev)
            return True if not still_pending else None

        _ingest.poll_until(
            _try_resolve_all,
            timeout=budget,
            provider="mem0",
            on_timeout_details={"phase": "wait_for_ingest", "ids": pending},
        )

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
        # Cursor validation runs BEFORE any upstream call (defence against
        # injection / quota-exhaustion via crafted cursor).
        page = _cursor.decode_cursor(cursor)
        try:
            # mem0 v2 rejects top-level entity kwargs; use filters= instead.
            result = self._client.get_all(
                version="v2",
                filters={"user_id": user_id},
                page_size=limit,
                page=page,
            )
        except Exception as exc:
            raise self._translate_error(exc) from exc

        if isinstance(result, dict) and "results" in result:
            items = result.get("results") or []
            has_next = result.get("next") is not None
        else:
            items = result if isinstance(result, list) else result.get("items", [])
            has_next = len(items) >= limit

        memories = [self._from_provider(r, status="done") for r in items]
        if scope is not None:
            from fnmatch import fnmatchcase
            memories = [
                m for m in memories if m.scope and fnmatchcase(m.scope, scope)
            ]
        if tag is not None:
            memories = [m for m in memories if m.tags and tag in m.tags]
        next_cursor = _cursor.encode_cursor(page + 1) if has_next else None
        return MemoryPage(items=memories, next_cursor=next_cursor)

    def search(
        self,
        query: str,
        user_id: str,
        *,
        scope: str | None = None,
        limit: int = 10,
        min_score: float | None = None,
    ) -> list[SearchResult]:
        # Pre-flight: empty user_id MUST raise BEFORE any upstream call to
        # prevent accidental cross-user broadening (FR-104 / data-model.md §3).
        require_user_id(user_id, provider="mem0")
        try:
            # mem0 v2 requires `filters={"user_id": ...}` — passing
            # `user_id=` as a top-level kwarg is rejected since 2.x.
            result = self._client.search(
                query=query,
                version="v2",
                filters={"user_id": user_id},
                limit=limit,
            )
        except Exception as exc:
            raise self._translate_error(exc) from exc
        items = result if isinstance(result, list) else result.get("results", [])
        out: list[SearchResult] = []
        for item in items:
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
            "audit is not supported by the mem0 provider", provider="mem0"
        )


__all__ = ["Mem0Adapter"]
