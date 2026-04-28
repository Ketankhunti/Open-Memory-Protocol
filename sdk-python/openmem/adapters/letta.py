"""LettaAdapter — translates OMP verbs to the Letta Python SDK.

Mapping: [contracts/letta-mapping.md](../../../specs/002-m2-pool-passthrough-adapters/contracts/letta-mapping.md).

Letta's primitive is the *agent* with archival memory blocks. We map
one OMP `user_id` → one Letta agent, created on first use and cached
by `user_id`. Passage IDs are agent-scoped, so the OMP id encodes both
agent + passage as ``mem_<agent_id>_<passage_id>``.
"""

from __future__ import annotations

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


class LettaAdapter(BaseAdapter):
    """Translate OMP verbs to the Letta SDK."""

    def __init__(
        self,
        api_key: str,
        base_url: str | None = None,
        *,
        client: Any = None,
    ) -> None:
        self._api_key = api_key
        self._base_url = base_url
        self._agent_cache: dict[str, str] = {}
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

    # ----------------------------------------------------- mapping

    @staticmethod
    def _translate_error(exc: BaseException) -> OMPError:
        name = type(exc).__name__
        msg = str(exc)
        mapping = {
            "UnauthorizedError": UnauthorizedError,
            "NotFoundError": NotFoundError,
            "BadRequestError": InvalidRequestError,
            "RateLimitError": RateLimitedError,
        }
        klass = mapping.get(name, ProviderError)
        return klass(msg, provider="letta")

    def _agent_for(self, user_id: str) -> str:
        if user_id in self._agent_cache:
            return self._agent_cache[user_id]
        try:
            agent = self._client.agents.create(name=f"omp_{user_id}")
        except Exception as exc:
            raise self._translate_error(exc) from exc
        agent_id = getattr(agent, "id", None) or agent["id"]
        self._agent_cache[user_id] = agent_id
        return agent_id

    @staticmethod
    def _passage_to_memory(passage: Any, agent_id: str, user_id: str) -> Memory:
        get = (
            (lambda k: getattr(passage, k, None))
            if not isinstance(passage, dict)
            else passage.get
        )
        passage_id = get("id")
        text = get("text") or ""
        created = get("created_at") or datetime.now(timezone.utc)
        if isinstance(created, str):
            created = datetime.fromisoformat(created.replace("Z", "+00:00"))
        meta = get("metadata") or {}
        if not isinstance(meta, dict):
            meta = {}
        kwargs: dict[str, Any] = {
            "id": _encode_id(agent_id, passage_id),
            "content": text,
            "user_id": user_id,
            "created_at": created,
            "scope": meta.get("scope"),
            "tags": meta.get("tags"),
        }
        for k, v in meta.items():
            if k.startswith("x-"):
                kwargs[k] = v
        kwargs.setdefault(
            "x-letta", passage if isinstance(passage, dict) else None
        )
        return Memory.model_validate(kwargs)

    # ----------------------------------------------------- capabilities

    _CAPS = Capabilities(
        provider="letta",
        omp_version="0.1",
        verbs=["add", "get", "delete", "list", "search", "context"],
        features=CapabilityFeatures(
            vector_search=True,
            keyword_search=False,
            temporal=True,
            scopes="native",
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
                f"verb {verb!r} not supported by letta", provider="letta"
            )

    # ----------------------------------------------------- verbs

    def add(self, memory: MemoryInput) -> Memory:
        self._check_verb("add")
        agent_id = self._agent_for(memory.user_id)
        meta: dict[str, Any] = {}
        if memory.scope is not None:
            meta["scope"] = memory.scope
        if memory.tags:
            meta["tags"] = memory.tags
        # x-* extension keys round-trip through metadata.
        for k, v in (memory.model_extra or {}).items():
            if k.startswith("x-"):
                meta[k] = v
        try:
            passage = self._client.agents.passages.create(
                agent_id=agent_id,
                text=memory.content,
                **({"metadata": meta} if meta else {}),
            )
        except Exception as exc:
            raise self._translate_error(exc) from exc
        if isinstance(passage, list) and passage:
            passage = passage[0]
        return self._passage_to_memory(passage, agent_id, memory.user_id)

    def get(self, id: str) -> Memory:
        self._check_verb("get")
        agent_id, passage_id = _decode_id(id)
        try:
            passage = self._client.agents.passages.retrieve(
                agent_id=agent_id, passage_id=passage_id
            )
        except Exception as exc:
            raise self._translate_error(exc) from exc
        # Reverse-lookup the user_id from the cache (best-effort).
        user_id = next(
            (uid for uid, aid in self._agent_cache.items() if aid == agent_id),
            "",
        )
        return self._passage_to_memory(passage, agent_id, user_id)

    def update(self, id: str, update: MemoryUpdate) -> Memory:
        self._check_verb("update")  # always raises
        raise AssertionError("unreachable")  # pragma: no cover

    def delete(self, id: str) -> None:
        self._check_verb("delete")
        agent_id, passage_id = _decode_id(id)
        try:
            self._client.agents.passages.delete(
                agent_id=agent_id, passage_id=passage_id
            )
        except Exception as exc:
            raise self._translate_error(exc) from exc

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
        agent_id = self._agent_for(user_id)
        try:
            passages = self._client.agents.passages.list(
                agent_id=agent_id, limit=limit, after=cursor
            )
        except Exception as exc:
            raise self._translate_error(exc) from exc
        items = [self._passage_to_memory(p, agent_id, user_id) for p in passages]
        if scope is not None:
            from fnmatch import fnmatchcase
            items = [m for m in items if m.scope and fnmatchcase(m.scope, scope)]
        if tag is not None:
            items = [m for m in items if m.tags and tag in m.tags]
        next_cursor = items[-1].id if len(items) >= limit else None
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
        agent_id = self._agent_for(user_id)
        try:
            results = self._client.agents.passages.search(
                agent_id=agent_id, query=query, limit=limit
            )
        except Exception as exc:
            raise self._translate_error(exc) from exc
        out: list[SearchResult] = []
        for item in results:
            passage = (
                item.get("passage", item) if isinstance(item, dict) else item
            )
            score = (
                float(item.get("score", 0.0))
                if isinstance(item, dict)
                else float(getattr(item, "score", 0.0))
            )
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
