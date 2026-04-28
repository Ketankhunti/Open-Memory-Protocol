"""Mem0Adapter — translates OMP verbs to the Mem0 Python SDK.

Mapping authority: [contracts/mem0-mapping.md](../../../specs/002-m2-pool-passthrough-adapters/contracts/mem0-mapping.md).

Mem0 is provider-managed embeddings: we never pass `embedding_model`
to the SDK (EC-007). Scope and tags both live in the free-form
`metadata` dict (EC-006).
"""

from __future__ import annotations

import base64
import json
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


def _encode_cursor(page: int) -> str:
    return base64.urlsafe_b64encode(
        json.dumps({"page": page}).encode("utf-8")
    ).decode("ascii")


def _decode_cursor(cursor: str | None) -> int:
    if cursor is None:
        return 1
    try:
        return int(json.loads(base64.urlsafe_b64decode(cursor)).get("page", 1))
    except Exception as exc:
        raise InvalidRequestError(
            f"invalid cursor: {cursor!r}", provider="mem0"
        ) from exc


class Mem0Adapter(BaseAdapter):
    """Translate OMP verbs to the Mem0 Python SDK."""

    def __init__(
        self,
        api_key: str,
        host: str = "https://api.mem0.ai",
        *,
        client: Any = None,
    ) -> None:
        self._api_key = api_key
        self._host = host
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
        # x-mem0 extension keys round-trip via metadata.
        extras = getattr(inp, "model_extra", None) or {}
        for k, v in extras.items():
            if k.startswith("x-"):
                meta[k] = v
        return meta

    @staticmethod
    def _from_provider(record: dict[str, Any]) -> Memory:
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
            "tags": meta.get("tags"),
            "source": source,
            "confidence": meta.get("confidence"),
            "valid_from": valid_from,
            "valid_to": valid_to,
            "supersedes": meta.get("supersedes"),
        }
        # Round-trip any x-* extension keys
        for k, v in meta.items():
            if k.startswith("x-"):
                kwargs[k] = v
        # Stash full provider record under x-mem0 for forward-compat
        kwargs.setdefault("x-mem0", record)
        return Memory.model_validate(kwargs)

    @staticmethod
    def _translate_error(exc: BaseException) -> OMPError:
        # Lazy attribute lookups keep this importable without mem0ai installed.
        name = type(exc).__name__
        msg = str(exc)
        mapping = {
            "AuthenticationError": UnauthorizedError,
            "NotFoundError": NotFoundError,
            "ValidationError": InvalidRequestError,
            "RateLimitError": RateLimitedError,
        }
        klass = mapping.get(name, ProviderError)
        return klass(msg, provider="mem0")

    # ---------------------------------------------------- capabilities

    _CAPS = Capabilities(
        provider="mem0",
        omp_version="0.1",
        verbs=["add", "get", "update", "delete", "list", "search", "context"],
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

    # ------------------------------------------------------------- verbs

    def add(self, memory: MemoryInput) -> Memory:
        meta = self._build_metadata(memory)
        try:
            result = self._client.add(
                messages=[{"role": "user", "content": memory.content}],
                user_id=memory.user_id,
                metadata=meta,
            )
        except Exception as exc:
            raise self._translate_error(exc) from exc
        # Mem0 may return list-of-results or a single record; normalize.
        if isinstance(result, list) and result:
            result = result[0]
        if not isinstance(result, dict):
            raise ProviderError(
                f"unexpected mem0.add response: {result!r}", provider="mem0"
            )
        return self._from_provider(result)

    def get(self, id: str) -> Memory:
        try:
            record = self._client.get(memory_id=id)
        except Exception as exc:
            raise self._translate_error(exc) from exc
        if record is None:
            raise NotFoundError(f"memory {id!r} not found", provider="mem0")
        return self._from_provider(record)

    def update(self, id: str, update: MemoryUpdate) -> Memory:
        try:
            if update.content is not None:
                self._client.update(memory_id=id, data=update.content)
            # Tags / scope changes piggy-back via a metadata patch.
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
                    self._client.update_metadata(memory_id=id, metadata=meta)
            record = self._client.get(memory_id=id)
        except Exception as exc:
            raise self._translate_error(exc) from exc
        return self._from_provider(record)

    def delete(self, id: str) -> None:
        try:
            self._client.delete(memory_id=id)
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
        page = _decode_cursor(cursor)
        try:
            result = self._client.get_all(
                user_id=user_id, limit=limit, page=page
            )
        except Exception as exc:
            raise self._translate_error(exc) from exc
        items = result if isinstance(result, list) else result.get("items", [])
        memories = [self._from_provider(r) for r in items]
        # Client-side scope / tag filtering (Mem0 has no native filters).
        if scope is not None:
            from fnmatch import fnmatchcase
            memories = [
                m for m in memories if m.scope and fnmatchcase(m.scope, scope)
            ]
        if tag is not None:
            memories = [m for m in memories if m.tags and tag in m.tags]
        next_cursor = (
            _encode_cursor(page + 1) if len(items) >= limit else None
        )
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
        try:
            result = self._client.search(
                query=query, user_id=user_id, limit=limit
            )
        except Exception as exc:
            raise self._translate_error(exc) from exc
        items = result if isinstance(result, list) else result.get("results", [])
        out: list[SearchResult] = []
        for item in items:
            mem = self._from_provider(item)
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
