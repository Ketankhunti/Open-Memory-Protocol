"""Async PostgreSQL + pgvector adapter (T015 / M3.2).

Native async mirror of :class:`openmem.adapters.postgres.PostgresAdapter`,
implemented on top of ``asyncpg``. Reuses the SQL strings from
:mod:`openmem.adapters._postgres_sql` to guarantee byte-identical
behaviour with the sync adapter (FR-011 / SC-008).

Cancellation contract (contracts/async-memory.md §3, C-CAN-1..3 — *Native* tier):

* All connection acquires use ``async with self._pool.acquire() as conn:``
  so cancelling the awaiter triggers ``__aexit__`` and the connection is
  released back to the pool within 500 ms (C-CAN-2).
* asyncpg propagates server-side query cancel via the wire protocol on
  task cancellation, so :data:`pg_stat_activity` MUST NOT show the
  cancelled query 1 s after the awaiter sees ``CancelledError`` (C-CAN-3).
"""

from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime, timezone
from typing import Any

import asyncpg

from ..errors import InvalidRequestError, NotFoundError, ProviderError
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
from ._postgres_sql import (
    CREATE_EXTENSION_SQL,
    DELETE_MEMORY_SQL,
    GET_MEMORY_SQL,
    INDEX_CREATED_AT_SQL,
    INDEX_TAGS_SQL,
    INDEX_USER_SCOPE_SQL,
    INSERT_MEMORY_SQL,
    decode_cursor as _decode_cursor,
    encode_cursor as _encode_cursor,
    make_create_table_sql,
    new_id as _new_id,
    scope_glob_to_sql_like as _scope_glob_to_sql_like,
    split_extensions as _split_extensions,
    vector_literal as _vector_literal,
)
from ._validation import require_user_id
from .embedder import Embedder, FakeEmbedder

__all__ = ["AsyncPostgresAdapter"]


# ---------------------------------------------------------------------------
# Placeholder translation: shared SQL uses psycopg-style ``%s``; asyncpg
# requires positional ``$1``, ``$2`` markers. Translation is purely
# textual (we never embed user input into the query template, only into
# parameter values), so it is safe.
# ---------------------------------------------------------------------------


_PLACEHOLDER_RE = re.compile(r"%s")


def _to_asyncpg_sql(sql: str) -> str:
    counter = {"n": 0}

    def _sub(_match: re.Match[str]) -> str:
        counter["n"] += 1
        return f"${counter['n']}"

    return _PLACEHOLDER_RE.sub(_sub, sql)


_INSERT_MEMORY_ASYNCPG = _to_asyncpg_sql(INSERT_MEMORY_SQL)
_GET_MEMORY_ASYNCPG = _to_asyncpg_sql(GET_MEMORY_SQL)
_DELETE_MEMORY_ASYNCPG = _to_asyncpg_sql(DELETE_MEMORY_SQL)


# ---------------------------------------------------------------------------
# Adapter
# ---------------------------------------------------------------------------


class AsyncPostgresAdapter:
    """Async OMP adapter backed by asyncpg + pgvector."""

    def __init__(
        self,
        url: str,
        *,
        embedder: Embedder | None = None,
        pool_min_size: int = 1,
        pool_max_size: int = 10,
        pool_timeout: float = 30.0,
    ) -> None:
        self._url = url
        self.embedder: Embedder = embedder or FakeEmbedder()
        self._pool_min_size = pool_min_size
        self._pool_max_size = pool_max_size
        self._pool_timeout = pool_timeout
        self._pool: asyncpg.Pool | None = None
        self._dim = self.embedder.dim
        self._schema_ready = False
        self._init_lock = asyncio.Lock()

    # ------------------------------------------------------- pool lifecycle

    async def _ensure_pool(self) -> asyncpg.Pool:
        """Lazy pool creation (C-LIFE-1: no I/O in __init__).

        Guarded by an asyncio.Lock so concurrent first-call fan-out
        (e.g. ``asyncio.gather(*[mem.add(...) for _ in range(100)])``)
        cannot race and trigger N parallel ``create_pool`` attempts —
        which would burst-open N×min_size connections and starve the
        Postgres ``max_connections`` cap.
        """
        if self._pool is not None and self._schema_ready:
            return self._pool
        async with self._init_lock:
            if self._pool is None:
                try:
                    self._pool = await asyncpg.create_pool(
                        dsn=self._url,
                        min_size=self._pool_min_size,
                        max_size=self._pool_max_size,
                        timeout=self._pool_timeout,
                    )
                except Exception as e:
                    raise ProviderError(
                        f"failed to open async postgres pool: {e}",
                        provider="postgres",
                    ) from e
            if not self._schema_ready:
                await self._ensure_schema()
                self._schema_ready = True
        assert self._pool is not None
        return self._pool

    async def _ensure_schema(self) -> None:
        assert self._pool is not None
        try:
            async with self._pool.acquire() as conn:
                await conn.execute(CREATE_EXTENSION_SQL)
                await conn.execute(make_create_table_sql(self._dim))
                await conn.execute(INDEX_USER_SCOPE_SQL)
                await conn.execute(INDEX_TAGS_SQL)
                await conn.execute(INDEX_CREATED_AT_SQL)
        except Exception as e:
            raise ProviderError(
                f"DDL failed: {e}", provider="postgres"
            ) from e

    async def close(self) -> None:
        """Close the pool. Idempotent."""
        pool = self._pool
        if pool is not None:
            self._pool = None
            try:
                await pool.close()
            except Exception:
                pass

    # ---------------------------------------------------------------- helpers

    def _row_to_memory(self, row: Any) -> Memory:
        d = dict(row)
        # asyncpg returns JSONB columns as `str`; psycopg returned dicts.
        # Normalise so the Memory model receives the same shape.
        if isinstance(d.get("source"), str):
            d["source"] = json.loads(d["source"])
        if isinstance(d.get("extensions"), str):
            d["extensions"] = json.loads(d["extensions"])
        data: dict[str, Any] = {
            "id": d["id"],
            "content": d["content"],
            "user_id": d["user_id"],
            "scope": d["scope"],
            "tags": d["tags"],
            "source": MemorySource(**d["source"]) if d.get("source") else None,
            "confidence": d["confidence"],
            "valid_from": d["valid_from"],
            "valid_to": d["valid_to"],
            "supersedes": d["supersedes"],
            "embedding_model": d["embedding_model"],
            "created_at": d["created_at"],
            "updated_at": d["updated_at"],
        }
        if d.get("extensions"):
            data.update(d["extensions"])
        return Memory(**data)

    # ------------------------------------------------------------------ verbs

    async def add(self, memory: MemoryInput) -> Memory:
        require_user_id(memory.user_id, provider="postgres")
        pool = await self._ensure_pool()
        if self.embedder.dim != self._dim:
            raise InvalidRequestError(
                f"embedder dim {self.embedder.dim} does not match "
                f"table dim {self._dim}",
                provider="postgres",
            )
        embedding = self.embedder.embed([memory.content])[0]
        new_id = _new_id()
        now = datetime.now(timezone.utc)
        extras = _split_extensions(memory.model_extra or {})
        try:
            async with pool.acquire() as conn:
                row = await conn.fetchrow(
                    _INSERT_MEMORY_ASYNCPG,
                    new_id,
                    memory.content,
                    memory.user_id,
                    memory.scope,
                    memory.tags,
                    json.dumps(memory.source.model_dump(exclude_none=True))
                    if memory.source
                    else None,
                    memory.confidence,
                    memory.valid_from,
                    memory.valid_to,
                    memory.supersedes,
                    self.embedder.model,
                    _vector_literal(embedding),
                    json.dumps(extras) if extras else None,
                    now,
                )
        except Exception as e:
            raise ProviderError(
                f"insert failed: {e}", provider="postgres"
            ) from e
        assert row is not None
        return self._row_to_memory(row)

    async def get(self, id: str) -> Memory:
        pool = await self._ensure_pool()
        try:
            async with pool.acquire() as conn:
                row = await conn.fetchrow(_GET_MEMORY_ASYNCPG, id)
        except Exception as e:
            raise ProviderError(
                f"get failed: {e}", provider="postgres"
            ) from e
        if row is None:
            raise NotFoundError(
                f"memory {id!r} not found", provider="postgres"
            )
        return self._row_to_memory(row)

    async def update(self, id: str, update: MemoryUpdate) -> Memory:
        pool = await self._ensure_pool()
        sets: list[str] = []
        params: list[Any] = []
        idx = 0

        def _next() -> str:
            nonlocal idx
            idx += 1
            return f"${idx}"

        if update.content is not None:
            sets.append(f"content = {_next()}")
            params.append(update.content)
            new_emb = self.embedder.embed([update.content])[0]
            sets.append(f"embedding = {_next()}::vector")
            params.append(_vector_literal(new_emb))
            sets.append(f"embedding_model = {_next()}")
            params.append(self.embedder.model)
        if update.scope is not None:
            sets.append(f"scope = {_next()}")
            params.append(update.scope)
        if update.tags is not None:
            sets.append(f"tags = {_next()}")
            params.append(update.tags)
        if update.confidence is not None:
            sets.append(f"confidence = {_next()}")
            params.append(update.confidence)
        if update.valid_to is not None:
            sets.append(f"valid_to = {_next()}")
            params.append(update.valid_to)
        if update.supersedes is not None:
            sets.append(
                f"supersedes = COALESCE(supersedes, ARRAY[]::TEXT[]) || {_next()}::TEXT[]"
            )
            params.append(update.supersedes)
        sets.append(f"updated_at = {_next()}")
        params.append(datetime.now(timezone.utc))

        if not sets or len(sets) == 1:  # only updated_at
            return await self.get(id)

        params.append(id)
        sql = (
            f"UPDATE memories SET {', '.join(sets)} "
            f"WHERE id = ${idx + 1} RETURNING *;"
        )
        try:
            async with pool.acquire() as conn:
                row = await conn.fetchrow(sql, *params)
        except Exception as e:
            raise ProviderError(
                f"update failed: {e}", provider="postgres"
            ) from e
        if row is None:
            raise NotFoundError(
                f"memory {id!r} not found", provider="postgres"
            )
        return self._row_to_memory(row)

    async def delete(self, id: str) -> None:
        pool = await self._ensure_pool()
        try:
            async with pool.acquire() as conn:
                result = await conn.execute(_DELETE_MEMORY_ASYNCPG, id)
        except Exception as e:
            raise ProviderError(
                f"delete failed: {e}", provider="postgres"
            ) from e
        # asyncpg `execute` returns a status string like "DELETE 1".
        try:
            affected = int(result.split()[-1])
        except (ValueError, IndexError):
            affected = 0
        if affected == 0:
            raise NotFoundError(
                f"memory {id!r} not found", provider="postgres"
            )

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
        require_user_id(user_id, provider="postgres")
        pool = await self._ensure_pool()
        clauses: list[str] = []
        params: list[Any] = []
        idx = 0

        def _next() -> str:
            nonlocal idx
            idx += 1
            return f"${idx}"

        clauses.append(f"user_id = {_next()}")
        params.append(user_id)
        if scope is not None:
            clauses.append(f"scope LIKE {_next()}")
            params.append(_scope_glob_to_sql_like(scope))
        if tag is not None:
            clauses.append(f"{_next()} = ANY(tags)")
            params.append(tag)
        if since is not None:
            clauses.append(f"created_at >= {_next()}")
            params.append(since)
        if until is not None:
            clauses.append(f"created_at <= {_next()}")
            params.append(until)
        if cursor is not None:
            ts, last_id = _decode_cursor(cursor)
            ph_ts = _next()
            ph_id = _next()
            clauses.append(f"(created_at, id) < ({ph_ts}, {ph_id})")
            params.extend([ts, last_id])

        ph_lim = _next()
        params.append(limit)
        sql = (
            "SELECT * FROM memories WHERE "
            + " AND ".join(clauses)
            + f" ORDER BY created_at DESC, id DESC LIMIT {ph_lim};"
        )
        try:
            async with pool.acquire() as conn:
                rows = await conn.fetch(sql, *params)
        except Exception as e:
            raise ProviderError(
                f"list failed: {e}", provider="postgres"
            ) from e

        items = [self._row_to_memory(r) for r in rows]
        next_cursor = (
            _encode_cursor(rows[-1]["created_at"], rows[-1]["id"])
            if len(rows) == limit and rows
            else None
        )
        return MemoryPage(items=items, next_cursor=next_cursor)

    async def search(
        self,
        query: str,
        user_id: str,
        *,
        scope: str | None = None,
        limit: int = 10,
        min_score: float | None = None,
    ) -> list[SearchResult]:
        require_user_id(user_id, provider="postgres")
        pool = await self._ensure_pool()
        q_emb = self.embedder.embed([query])[0]
        idx = 0

        def _next() -> str:
            nonlocal idx
            idx += 1
            return f"${idx}"

        ph_vec = _next()
        ph_user = _next()
        ph_model = _next()
        params: list[Any] = [_vector_literal(q_emb), user_id, self.embedder.model]
        clauses = [f"user_id = {ph_user}", f"embedding_model = {ph_model}"]
        if scope is not None:
            clauses.append(f"scope LIKE {_next()}")
            params.append(_scope_glob_to_sql_like(scope))
        ph_vec2 = _next()
        params.append(_vector_literal(q_emb))
        ph_lim = _next()
        params.append(limit)
        sql = (
            f"SELECT *, 1 - (embedding <=> {ph_vec}::vector) AS score FROM memories "
            f"WHERE {' AND '.join(clauses)} "
            f"ORDER BY embedding <=> {ph_vec2}::vector ASC LIMIT {ph_lim};"
        )
        try:
            async with pool.acquire() as conn:
                rows = await conn.fetch(sql, *params)
        except Exception as e:
            raise ProviderError(
                f"search failed: {e}", provider="postgres"
            ) from e

        if not rows:
            try:
                async with pool.acquire() as conn:
                    if scope is not None:
                        check = await conn.fetchval(
                            "SELECT 1 FROM memories WHERE user_id = $1 "
                            "AND scope LIKE $2 LIMIT 1;",
                            user_id,
                            _scope_glob_to_sql_like(scope),
                        )
                    else:
                        check = await conn.fetchval(
                            "SELECT 1 FROM memories WHERE user_id = $1 LIMIT 1;",
                            user_id,
                        )
                    has_other_models = check is not None
            except Exception:
                has_other_models = False
            if has_other_models:
                raise InvalidRequestError(
                    f"no memories indexed with model "
                    f"{self.embedder.model!r} for this user/scope",
                    provider="postgres",
                )
            return []

        results: list[SearchResult] = []
        for r in rows:
            d = dict(r)
            score = float(d.pop("score"))
            if min_score is not None and score < min_score:
                continue
            results.append(SearchResult(memory=self._row_to_memory(d), score=score))
        return results

    async def context(
        self,
        query: str,
        user_id: str,
        *,
        scope: str | None = None,
        token_budget: int = 500,
    ) -> ContextBlock:
        try:
            results = await self.search(
                query, user_id, scope=scope, limit=max(1, token_budget // 50)
            )
        except InvalidRequestError:
            results = []

        if not results:
            return ContextBlock(text="", citations=[], token_count=0)

        lines: list[str] = []
        citations: list[_Citation] = []
        running_chars = 0
        char_budget = token_budget * 4
        for i, r in enumerate(results, start=1):
            line = f"[{i}] {r.memory.content}"
            if running_chars + len(line) > char_budget and citations:
                break
            lines.append(line)
            citations.append(_Citation(memory_id=r.memory.id, score=r.score))
            running_chars += len(line) + 1
        text = "\n".join(lines)
        return ContextBlock(
            text=text, citations=citations, token_count=len(text) // 4
        )

    async def capabilities(self) -> Capabilities:
        return Capabilities(
            omp_version="0.1",
            provider="postgres",
            verbs=["add", "search", "get", "update", "delete", "list", "context"],
            features=CapabilityFeatures(
                vector_search=True,
                keyword_search=True,
                graph_queries=False,
                temporal=True,
                scopes="native",
                max_content_length=10000,
                supports_e2e=False,
                supports_audit=False,
                supports_supersession=True,
            ),
            limits=CapabilityLimits(max_search_results=100),
        )

    async def audit(
        self,
        user_id: str,
        *,
        app: str | None = None,
        since: datetime | None = None,
        limit: int = 100,
    ) -> list[AuditEntry]:
        from ..errors import UnsupportedCapabilityError

        raise UnsupportedCapabilityError(
            "audit is not supported by this provider",
            provider="postgres",
        )

    async def wait_for_ingest(
        self,
        ids: list[str],
        user_id: str,
        *,
        timeout: float | None = None,
    ) -> None:
        # Postgres is read-after-write — no polling needed.
        return None
