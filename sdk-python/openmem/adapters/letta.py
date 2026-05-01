"""LettaAdapter — translates OMP verbs to the Letta Python SDK (M2.1).

Mapping authority: [contracts/letta-mapping.md](../../../specs/003-m2-1-live/contracts/letta-mapping.md).

M2.1 changes vs M2:
- Constructor uses `api_key=` (not `token=` — gone in letta-client 1.10).
- `passages.create` returns `list[Passage]`; first id is canonical, all
  passage ids stash under `x-letta.passage_ids` (FR-113 / EC-104).
- `get`, `update` excluded from advertised verbs (FR-116); calling either
  raises `UnsupportedCapabilityError` BEFORE any network call.
- `passages.search` uses `top_k=` (NOT `limit=`) per FR-115.
- `delete` iterates over EVERY passage id under `x-letta.passage_ids`;
  the kwarg name (`passage_id` / `id` / `memory_id`) is introspected at
  init time from `inspect.signature(passages.delete).parameters`.
- `_agent_for(user_id)` is cached; on NotFound the cache entry is
  invalidated and re-created on the next call (FR-117).
- Empty `user_id` rejected BEFORE any upstream call (cross-user scoping
  defence).
"""

from __future__ import annotations

import inspect
import logging
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
from .base import BaseAdapter

_LOG = logging.getLogger(__name__)

# Candidate kwarg names for `passages.delete` (in preference order).
_DELETE_KWARG_CANDIDATES = ("passage_id", "id", "memory_id")


def _encode_id(agent_id: str, passage_id: str) -> str:
    return f"mem_{agent_id}_{passage_id}"


def _decode_id(omp_id: str) -> tuple[str, str]:
    if not omp_id.startswith("mem_"):
        raise InvalidRequestError(
            f"not a Letta-encoded id: {omp_id!r}", provider="letta"
        )
    rest = omp_id[len("mem_"):]
    sep = rest.rfind("_")
    if sep < 0:
        raise InvalidRequestError(
            f"malformed Letta id: {omp_id!r}", provider="letta"
        )
    return rest[:sep], rest[sep + 1:]


def _encode_letta_after(passage_id: str) -> str:
    """Encode an opaque pagination cursor wrapping a raw passage id.

    Letta's `passages.list(after=…)` is keyed by passage id (not page
    number). To keep OMP cursors opaque to callers we wrap the id in a
    minimal JSON envelope and base64-url it.
    """
    import base64
    import json

    raw = json.dumps({"after": passage_id}, separators=(",", ":")).encode("ascii")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _decode_letta_after(cursor: str | None) -> str | None:
    """Decode an opaque cursor produced by ``_encode_letta_after``.

    Returns None when ``cursor`` is None / empty. Raises
    :class:`InvalidRequestError` on malformed input (cursor-injection
    defence).
    """
    if not cursor:
        return None
    import base64
    import binascii
    import json

    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        raw = base64.urlsafe_b64decode(padded.encode("ascii"))
        obj = json.loads(raw)
    except (ValueError, binascii.Error, json.JSONDecodeError) as exc:
        raise InvalidRequestError(
            f"malformed cursor: {cursor!r}", provider="letta"
        ) from exc
    after = obj.get("after") if isinstance(obj, dict) else None
    if not isinstance(after, str) or not after:
        raise InvalidRequestError(
            f"cursor missing 'after' id: {cursor!r}", provider="letta"
        )
    return after


class LettaAdapter(BaseAdapter):
    """Translate OMP verbs to the Letta SDK."""

    def __init__(
        self,
        api_key: str,
        base_url: str | None = None,
        *,
        client: Any = None,
    ) -> None:
        # api_key is private; never logged or surfaced (FR-118 / SC-107).
        self._api_key = api_key
        self._base_url = base_url
        self._agent_cache: dict[str, str] = {}
        # OMP-id → list[upstream passage id] for multi-passage delete (FR-114).
        self._passages_by_id: dict[str, list[str]] = {}
        if client is not None:
            self._client = client
        else:
            try:
                from letta_client import Letta  # type: ignore
            except ImportError as exc:  # pragma: no cover
                raise ImportError(
                    "LettaAdapter requires the `letta-client` package; "
                    "run `pip install openmem[letta]`"
                ) from exc
            kwargs: dict[str, Any] = {"api_key": api_key}
            if base_url:
                kwargs["base_url"] = base_url
            self._client = Letta(**kwargs)

        # Resolve the actual kwarg name used by passages.delete in the SDK
        # version we have. Cache for the lifetime of the adapter.
        self._delete_kwarg = self._resolve_delete_kwarg()

    def _resolve_delete_kwarg(self) -> str:
        """Pick the kwarg name accepted by `passages.delete` (FR-114)."""
        params: set[str] = set()
        try:
            delete_fn = self._client.agents.passages.delete
            sig = inspect.signature(delete_fn)
            params = set(sig.parameters)
            # MagicMocks expose a generic (*args, **kwargs) signature; treat
            # that as "no useful introspection available" and fall back.
            useful = params - {"args", "kwargs"}
            if not useful:
                params = set()
            else:
                params = useful
        except (AttributeError, ValueError, TypeError):
            params = set()
        for cand in _DELETE_KWARG_CANDIDATES:
            if cand in params:
                return cand
        # No introspection (e.g. MagicMock, or signature stripped) → assume
        # `passage_id` (the conftest shim's signature and the most common).
        if not params:
            return "passage_id"
        # SDK has a delete method but none of our candidate kwargs match
        # — caller MUST pin upstream to a known-good version.
        raise ProviderError(
            "cannot determine passages.delete kwarg name; "
            "pin letta-client to a supported version",
            provider="letta",
        )

    # ----------------------------------------------------- mapping

    @staticmethod
    def _translate_error(exc: BaseException) -> OMPError:
        if isinstance(exc, OMPError):
            return exc
        name = type(exc).__name__
        msg = str(exc)
        mapping = {
            "UnauthorizedError": UnauthorizedError,
            "AuthenticationError": UnauthorizedError,
            "NotFoundError": NotFoundError,
            "BadRequestError": InvalidRequestError,
            "ValidationError": InvalidRequestError,
            "RateLimitError": RateLimitedError,
        }
        klass = mapping.get(name, ProviderError)
        return klass(msg, provider="letta")

    def _agent_for(self, user_id: str) -> str:
        """Cached `user_id → agent_id`; invalidate-and-retry on NotFound."""
        if user_id in self._agent_cache:
            return self._agent_cache[user_id]
        try:
            agent = self._client.agents.create(name=f"omp_{user_id}")
        except Exception as exc:
            raise self._translate_error(exc) from exc
        agent_id = (
            getattr(agent, "id", None)
            or (agent["id"] if isinstance(agent, dict) else None)
        )
        if not agent_id:
            raise ProviderError(
                "agents.create returned no id", provider="letta"
            )
        self._agent_cache[user_id] = str(agent_id)
        return self._agent_cache[user_id]

    def _invalidate_agent(self, user_id: str) -> None:
        self._agent_cache.pop(user_id, None)

    @staticmethod
    def _passage_to_memory(
        passage: Any,
        agent_id: str,
        user_id: str,
        *,
        passage_ids: list[str] | None = None,
    ) -> Memory:
        get = (
            (lambda k: getattr(passage, k, None))
            if not isinstance(passage, dict)
            else passage.get
        )
        passage_id = get("id")
        # Real Letta returns `content`; mock shims may use `text`.
        text = get("content") or get("text") or ""
        created = (
            get("created_at") or get("createdAt") or get("timestamp")
            or datetime.now(timezone.utc)
        )
        if isinstance(created, str):
            created = datetime.fromisoformat(created.replace("Z", "+00:00"))
        meta = get("metadata") or {}
        if not isinstance(meta, dict):
            meta = {}
        # M2.1: live Letta exposes `tags=[...]` natively but does NOT
        # store arbitrary metadata. Our `add()` encodes scope and `x-…`
        # extension keys into the tag list using reserved prefixes;
        # decode them back here so the OMP wire shape round-trips.
        raw_tags = get("tags") or meta.get("tags") or []
        if not isinstance(raw_tags, list):
            raw_tags = []
        decoded_tags: list[str] = []
        decoded_scope: str | None = meta.get("scope")
        decoded_x: dict[str, Any] = {}
        for t in raw_tags:
            ts = str(t)
            if ts.startswith("_omp_scope:"):
                decoded_scope = ts[len("_omp_scope:"):]
            elif ts.startswith("_omp_x:"):
                # _omp_x:<key>:<value>
                payload = ts[len("_omp_x:"):]
                k, _, v = payload.partition(":")
                if k:
                    decoded_x[k] = v
            else:
                decoded_tags.append(ts)
        kwargs: dict[str, Any] = {
            "id": _encode_id(agent_id, passage_id),
            "content": text,
            "user_id": user_id,
            "created_at": created,
            "scope": decoded_scope,
            "tags": decoded_tags or None,
            "status": "done",
        }
        for k, v in decoded_x.items():
            kwargs[k] = v
        for k, v in meta.items():
            if k.startswith("x-"):
                kwargs[k] = v
        # x-letta extension carries agent_id + ALL passage_ids for delete-fanout.
        x_letta = {"agent_id": agent_id}
        if passage_ids:
            x_letta["passage_ids"] = list(passage_ids)
        else:
            x_letta["passage_ids"] = [str(passage_id)]
        kwargs["x-letta"] = x_letta
        return Memory.model_validate(kwargs)

    # ----------------------------------------------------- capabilities

    _CAPS = Capabilities(
        provider="letta",
        omp_version="0.1",
        # FR-116: NO `get`, NO `update` (we drop them since real Letta has
        # no `passages.retrieve` and no passage-level update).
        verbs=["add", "delete", "list", "search", "context"],
        features=CapabilityFeatures.model_validate(
            {
                "vector_search": True,
                "keyword_search": False,
                "temporal": True,
                "scopes": "native",
                "supports_supersession": False,
                "supports_audit": False,
                "max_content_length": 10000,
                "status_field": True,
                "async_ingestion": False,
                "auto_chunking": True,
            }
        ),
        limits=CapabilityLimits(),
    )

    def capabilities(self) -> Capabilities:
        return self._CAPS

    def _check_verb(self, verb: str) -> None:
        if verb not in self._CAPS.verbs:
            raise UnsupportedCapabilityError(
                f"verb {verb!r} not supported by letta", provider="letta"
            )

    # ----------------------------------------------------- verbs

    def add(self, memory: MemoryInput) -> Memory:
        self._check_verb("add")
        if not memory.user_id or not str(memory.user_id).strip():
            raise InvalidRequestError("user_id is required", provider="letta")
        agent_id = self._agent_for(memory.user_id)
        # M2.1: live letta `passages.create` only persists `text=` and
        # `tags=` — there is NO `metadata=` parameter (verified against
        # letta-client 1.10.x). Encode OMP scope + arbitrary `x-…`
        # extension keys into the native `tags=` field using a reserved
        # prefix so we can decode them back on read.
        live_tags: list[str] = []
        if memory.tags:
            live_tags.extend(str(t) for t in memory.tags)
        if memory.scope is not None:
            live_tags.append(f"_omp_scope:{memory.scope}")
        for k, v in (memory.model_extra or {}).items():
            if k.startswith("x-") and isinstance(v, (str, int, float, bool)):
                live_tags.append(f"_omp_x:{k}:{v}")
        create_kwargs: dict[str, Any] = {
            "agent_id": agent_id,
            "text": memory.content,
        }
        if live_tags:
            create_kwargs["tags"] = live_tags
        try:
            result = self._client.agents.passages.create(**create_kwargs)
        except Exception as exc:
            raise self._translate_error(exc) from exc

        # M2.1: real Letta returns list[Passage] (LLM auto-chunks long text).
        passages = result if isinstance(result, list) else [result]
        if not passages:
            raise ProviderError(
                "passages.create returned empty list", provider="letta"
            )
        first = passages[0]
        # Extract every passage id for fan-out delete (FR-114).
        passage_ids: list[str] = []
        for p in passages:
            pid = (
                p.get("id") if isinstance(p, dict) else getattr(p, "id", None)
            )
            if pid is not None:
                passage_ids.append(str(pid))
        mem = self._passage_to_memory(
            first, agent_id, memory.user_id, passage_ids=passage_ids
        )
        # Override content with the ORIGINAL (Letta does not LLM-rewrite,
        # but if SDK echoed something different we still want fidelity).
        mem = mem.model_copy(update={"content": memory.content})
        # Cache passage ids by OMP id for delete iteration.
        self._passages_by_id[mem.id] = passage_ids
        return mem

    def get(self, id: str) -> Memory:
        # FR-116: explicitly unsupported — never reaches the network.
        self._check_verb("get")  # always raises
        raise AssertionError("unreachable")  # pragma: no cover

    def update(self, id: str, update: MemoryUpdate) -> Memory:
        self._check_verb("update")  # always raises
        raise AssertionError("unreachable")  # pragma: no cover

    def delete(self, id: str) -> None:
        self._check_verb("delete")
        agent_id, parsed_passage_id = _decode_id(id)
        passage_ids = self._passages_by_id.get(id) or [parsed_passage_id]
        deleted_any = False
        last_exc: Exception | None = None
        for pid in passage_ids:
            try:
                self._client.agents.passages.delete(
                    agent_id=agent_id,
                    **{self._delete_kwarg: pid},
                )
                deleted_any = True
            except Exception as exc:  # noqa: BLE001 - per-passage tolerance
                err = self._translate_error(exc)
                if isinstance(err, NotFoundError):
                    deleted_any = True  # already gone → still success
                    continue
                last_exc = err
                # Log per-passage failure WITHOUT any sensitive content
                _LOG.warning(
                    "letta passage delete failed: agent=%s passage=%s err=%s",
                    agent_id,
                    pid,
                    type(err).__name__,
                )
        # Drop cache entry only when we know all passages are gone (or were).
        self._passages_by_id.pop(id, None)
        if not deleted_any and last_exc is not None:
            raise last_exc

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
        if not user_id or not str(user_id).strip():
            raise InvalidRequestError("user_id is required", provider="letta")
        agent_id = self._agent_for(user_id)
        # M2.1: Letta paginates by `after=<passage_id>` (NOT by page number).
        # Decode the OMP cursor into a letta-native passage id so the upstream
        # call gets the right shape. Cursors emitted by this adapter wrap the
        # raw passage id in a JSON envelope so they remain OMP-opaque.
        after_id = _decode_letta_after(cursor)
        agent_state = {"id": agent_id}

        def _do_list(after: str | None):
            try:
                return self._client.agents.passages.list(
                    agent_id=agent_state["id"], limit=limit, after=after
                )
            except Exception as exc:
                err = self._translate_error(exc)
                if isinstance(err, NotFoundError):
                    self._invalidate_agent(user_id)
                    agent_state["id"] = self._agent_for(user_id)
                    return self._client.agents.passages.list(
                        agent_id=agent_state["id"], limit=limit, after=after
                    )
                raise err from exc

        passages = _do_list(after_id)
        agent_id = agent_state["id"]
        items = [self._passage_to_memory(p, agent_id, user_id) for p in passages]
        if scope is not None:
            from fnmatch import fnmatchcase
            items = [m for m in items if m.scope and fnmatchcase(m.scope, scope)]
        if tag is not None:
            items = [m for m in items if m.tags and tag in m.tags]
        # FR-115: Letta has no native cursor; emit our own opaque cursor
        # carrying the last raw passage id so the next call can resume.
        if len(passages) >= limit:
            last_passage_id = (
                passages[-1].get("id")
                if isinstance(passages[-1], dict)
                else getattr(passages[-1], "id", None)
            )
            next_cursor = (
                _encode_letta_after(str(last_passage_id))
                if last_passage_id
                else None
            )
        else:
            next_cursor = None
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
        if not user_id or not str(user_id).strip():
            raise InvalidRequestError("user_id is required", provider="letta")
        agent_id = self._agent_for(user_id)
        try:
            # M2.1: top_k=limit (NOT limit=). Tag filtering deferred (FR-115).
            results = self._client.agents.passages.search(
                agent_id=agent_id, query=query, top_k=limit
            )
        except TypeError:
            # Older / mock SDKs accept `limit=`; degrade gracefully.
            try:
                results = self._client.agents.passages.search(
                    agent_id=agent_id, query=query, limit=limit
                )
            except Exception as exc:
                raise self._translate_error(exc) from exc
        except Exception as exc:
            raise self._translate_error(exc) from exc
        # PassageSearchResponse may be:
        #   - object with `.results` attr (live letta-client>=1.10),
        #   - dict {count, results:[...]},
        #   - or just a plain list (older / mock shims).
        if isinstance(results, dict) and "results" in results:
            iterable = results.get("results") or []
        elif hasattr(results, "results"):
            iterable = getattr(results, "results") or []
        else:
            iterable = results

        out: list[SearchResult] = []
        for item in iterable:
            # Tolerate both flat `{id, content, ...}` and nested
            # `{passage:{id,text,...}, score:...}` shapes.
            if isinstance(item, dict) and "passage" in item:
                passage = item["passage"]
                raw_score = item.get("score")
            else:
                passage = item
                if isinstance(item, dict):
                    raw_score = item.get("score")
                else:
                    raw_score = getattr(item, "score", None)
            # Real PassageSearchResponse rows carry no score; default to 0.0
            # so the OMP SearchResult contract (score: float) is satisfied.
            score = float(raw_score) if raw_score is not None else 0.0
            mem = self._passage_to_memory(passage, agent_id, user_id)
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
            "audit is not supported by letta", provider="letta"
        )


__all__ = ["LettaAdapter"]
