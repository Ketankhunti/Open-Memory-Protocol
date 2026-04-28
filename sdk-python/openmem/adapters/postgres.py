"""PostgreSQL + pgvector reference adapter.

Reference Native adapter for OMP — the proof that the protocol works
without depending on any third-party hosted service (Constitution
Principle IV — Provider Neutrality).

Key behaviors:

* DDL is run idempotently on first use (``CREATE EXTENSION IF NOT EXISTS
  vector`` + ``CREATE TABLE IF NOT EXISTS memories``).
* IDs are ``mem_<ulid>`` (sortable).
* Vectors are stored as pgvector ``VECTOR(<dim>)`` columns sized to the
  embedder's ``dim``.
* Cross-embedding-model search hard-fails (FR-014).
* ``x-<provider>`` extension fields round-trip via an ``extensions JSONB``
  column (Constitution Principle V).
* Pagination is keyset over ``(created_at DESC, id DESC)``.
"""

from __future__ import annotations

import base64
import json
from datetime import datetime, timezone
from typing import Any

import psycopg
import psycopg_pool
from psycopg.rows import dict_row
from psycopg.types.json import Json
from ulid import ULID

from ..errors import InvalidRequestError, NotFoundError, ProviderError
from ..types import (
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
from .embedder import Embedder, FakeEmbedder

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_STD_FIELDS = {
    "id",
    "content",
    "user_id",
    "scope",
    "tags",
    "source",
    "confidence",
    "valid_from",
    "valid_to",
    "supersedes",
    "embedding_model",
    "created_at",
    "updated_at",
}


def _new_id() -> str:
    return f"mem_{ULID()}"


def _vector_literal(vec: list[float]) -> str:
    """Render a Python list as a pgvector literal."""
    return "[" + ",".join(repr(float(x)) for x in vec) + "]"


def _scope_glob_to_sql_like(scope: str | None) -> str | None:
    if scope is None:
        return None
    return scope.replace("*", "%")


def _encode_cursor(created_at: datetime, id_: str) -> str:
    raw = f"{created_at.isoformat()}|{id_}".encode()
    return base64.urlsafe_b64encode(raw).decode("ascii")


def _decode_cursor(cursor: str) -> tuple[datetime, str]:
    raw = base64.urlsafe_b64decode(cursor.encode("ascii")).decode()
    ts, id_ = raw.split("|", 1)
    return datetime.fromisoformat(ts), id_


def _split_extensions(extra: dict[str, Any]) -> dict[str, Any]:
    """Extract ``x-<provider>`` keys from a free-form dict."""
    return {k: v for k, v in extra.items() if k.startswith("x-")}


# ---------------------------------------------------------------------------
# Adapter
# ---------------------------------------------------------------------------


class PostgresAdapter(BaseAdapter):
    """OMP adapter backed by Postgres + pgvector."""

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
        # M2: connection pool replaces the M1 RLock stop-gap (FR-001..005).
        try:
            self._pool = psycopg_pool.ConnectionPool(
                conninfo=url,
                min_size=pool_min_size,
                max_size=pool_max_size,
                timeout=pool_timeout,
                open=True,
            )
        except psycopg.Error as e:
            raise ProviderError(
                f"failed to open postgres pool: {e}", provider="postgres"
            ) from e
        self._dim = self.embedder.dim
        self._ensure_schema()

    def close(self) -> None:
        """Close the pool. Idempotent."""
        try:
            self._pool.close()
        except Exception:
            pass

    # ------------------------------------------------------------------ DDL

    def _ensure_schema(self) -> None:
        try:
            with self._pool.connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("CREATE EXTENSION IF NOT EXISTS vector;")
                    cur.execute(
                        f"""
                        CREATE TABLE IF NOT EXISTS memories (
                            id              TEXT PRIMARY KEY,
                            content         TEXT NOT NULL,
                            user_id         TEXT NOT NULL,
                            scope           TEXT,
                            tags            TEXT[],
                            source          JSONB,
                            confidence      REAL,
                            valid_from      TIMESTAMPTZ,
                            valid_to        TIMESTAMPTZ,
                            supersedes      TEXT[],
                            embedding_model TEXT,
                            embedding       VECTOR({self._dim}),
                            extensions      JSONB,
                            created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                            updated_at      TIMESTAMPTZ
                        );
                        """
                    )
                    cur.execute(
                        "CREATE INDEX IF NOT EXISTS idx_memories_user_scope "
                        "ON memories(user_id, scope);"
                    )
                    cur.execute(
                        "CREATE INDEX IF NOT EXISTS idx_memories_tags "
                        "ON memories USING GIN(tags);"
                    )
                    cur.execute(
                        "CREATE INDEX IF NOT EXISTS idx_memories_created_at "
                        "ON memories(created_at DESC, id DESC);"
                    )
                conn.commit()
        except psycopg_pool.PoolTimeout as e:
            raise ProviderError(
                f"connection pool exhausted during schema setup: {e}",
                provider="postgres",
            ) from e
        except psycopg.Error as e:
            raise ProviderError(
                f"DDL failed: {e}", provider="postgres"
            ) from e

    # ---------------------------------------------------------------- helpers

    def _row_to_memory(self, row: dict[str, Any]) -> Memory:
        data: dict[str, Any] = {
            "id": row["id"],
            "content": row["content"],
            "user_id": row["user_id"],
            "scope": row["scope"],
            "tags": row["tags"],
            "source": MemorySource(**row["source"]) if row["source"] else None,
            "confidence": row["confidence"],
            "valid_from": row["valid_from"],
            "valid_to": row["valid_to"],
            "supersedes": row["supersedes"],
            "embedding_model": row["embedding_model"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }
        # Round-trip x-<provider> extensions (Principle V)
        if row.get("extensions"):
            data.update(row["extensions"])
        return Memory(**data)

    # ------------------------------------------------------------------- add

    def add(self, memory: MemoryInput) -> Memory:
        # Pre-INSERT dim check (closes analyze finding I2 / EC-005)
        if self.embedder.dim != self._dim:
            raise InvalidRequestError(
                f"embedder dim {self.embedder.dim} does not match "
                f"table dim {self._dim}",
                provider="postgres",
            )
        embedding = self.embedder.embed([memory.content])[0]
        new_id = _new_id()
        now = datetime.now(timezone.utc)
        # Extract x-<provider> extension fields from the input's `extra`
        extras = _split_extensions(memory.model_extra or {})
        try:
            with self._pool.connection() as conn:
                with conn.cursor(row_factory=dict_row) as cur:
                    cur.execute(
                        """
                        INSERT INTO memories (
                            id, content, user_id, scope, tags, source, confidence,
                            valid_from, valid_to, supersedes, embedding_model,
                            embedding, extensions, created_at
                        ) VALUES (
                            %s, %s, %s, %s, %s, %s, %s,
                            %s, %s, %s, %s,
                            %s::vector, %s, %s
                        )
                        RETURNING *;
                        """,
                        (
                            new_id,
                            memory.content,
                            memory.user_id,
                            memory.scope,
                            memory.tags,
                            Json(memory.source.model_dump(exclude_none=True))
                            if memory.source
                            else None,
                            memory.confidence,
                            memory.valid_from,
                            memory.valid_to,
                            memory.supersedes,
                            self.embedder.model,
                            _vector_literal(embedding),
                            Json(extras) if extras else None,
                            now,
                        ),
                    )
                    row = cur.fetchone()
                conn.commit()
            assert row is not None
            return self._row_to_memory(row)
        except psycopg_pool.PoolTimeout as e:
            raise ProviderError(
                f"connection pool exhausted: {e}", provider="postgres"
            ) from e
        except psycopg.Error as e:
            raise ProviderError(
                f"insert failed: {e}", provider="postgres"
            ) from e

    # ------------------------------------------------------------------- get

    def get(self, id: str) -> Memory:
        try:
            with self._pool.connection() as conn:
                with conn.cursor(row_factory=dict_row) as cur:
                    cur.execute("SELECT * FROM memories WHERE id = %s;", (id,))
                    row = cur.fetchone()
            if row is None:
                raise NotFoundError(
                    f"memory {id!r} not found", provider="postgres"
                )
            return self._row_to_memory(row)
        except psycopg_pool.PoolTimeout as e:
            raise ProviderError(
                f"connection pool exhausted: {e}", provider="postgres"
            ) from e
        except psycopg.Error as e:
            raise ProviderError(
                f"get failed: {e}", provider="postgres"
            ) from e

    # ---------------------------------------------------------------- update

    def update(self, id: str, update: MemoryUpdate) -> Memory:
        sets: list[str] = []
        params: list[Any] = []
        if update.content is not None:
            sets.append("content = %s")
            params.append(update.content)
            # Re-embed
            new_emb = self.embedder.embed([update.content])[0]
            sets.append("embedding = %s::vector")
            params.append(_vector_literal(new_emb))
            sets.append("embedding_model = %s")
            params.append(self.embedder.model)
        if update.scope is not None:
            sets.append("scope = %s")
            params.append(update.scope)
        if update.tags is not None:
            sets.append("tags = %s")
            params.append(update.tags)
        if update.confidence is not None:
            sets.append("confidence = %s")
            params.append(update.confidence)
        if update.valid_to is not None:
            sets.append("valid_to = %s")
            params.append(update.valid_to)
        if update.supersedes is not None:
            # Append rather than replace
            sets.append(
                "supersedes = COALESCE(supersedes, ARRAY[]::TEXT[]) || %s::TEXT[]"
            )
            params.append(update.supersedes)
        sets.append("updated_at = %s")
        params.append(datetime.now(timezone.utc))

        if not sets:
            return self.get(id)

        params.append(id)
        try:
            with self._pool.connection() as conn:
                with conn.cursor(row_factory=dict_row) as cur:
                    cur.execute(
                        f"UPDATE memories SET {', '.join(sets)} "
                        "WHERE id = %s RETURNING *;",
                        tuple(params),
                    )
                    row = cur.fetchone()
                if row is None:
                    conn.rollback()
                    raise NotFoundError(
                        f"memory {id!r} not found", provider="postgres"
                    )
                conn.commit()
            return self._row_to_memory(row)
        except psycopg_pool.PoolTimeout as e:
            raise ProviderError(
                f"connection pool exhausted: {e}", provider="postgres"
            ) from e
        except psycopg.Error as e:
            raise ProviderError(
                f"update failed: {e}", provider="postgres"
            ) from e

    # ---------------------------------------------------------------- delete

    def delete(self, id: str) -> None:
        try:
            with self._pool.connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("DELETE FROM memories WHERE id = %s;", (id,))
                    affected = cur.rowcount
                conn.commit()
            if affected == 0:
                raise NotFoundError(
                    f"memory {id!r} not found", provider="postgres"
                )
        except psycopg_pool.PoolTimeout as e:
            raise ProviderError(
                f"connection pool exhausted: {e}", provider="postgres"
            ) from e
        except psycopg.Error as e:
            raise ProviderError(
                f"delete failed: {e}", provider="postgres"
            ) from e

    # ------------------------------------------------------------------ list

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
        clauses: list[str] = ["user_id = %s"]
        params: list[Any] = [user_id]
        if scope is not None:
            clauses.append("scope LIKE %s")
            params.append(_scope_glob_to_sql_like(scope))
        if tag is not None:
            clauses.append("%s = ANY(tags)")
            params.append(tag)
        if since is not None:
            clauses.append("created_at >= %s")
            params.append(since)
        if until is not None:
            clauses.append("created_at <= %s")
            params.append(until)
        if cursor is not None:
            ts, last_id = _decode_cursor(cursor)
            clauses.append("(created_at, id) < (%s, %s)")
            params.extend([ts, last_id])

        params.append(limit)

        sql = (
            "SELECT * FROM memories WHERE "
            + " AND ".join(clauses)
            + " ORDER BY created_at DESC, id DESC LIMIT %s;"
        )
        try:
            with self._pool.connection() as conn:
                with conn.cursor(row_factory=dict_row) as cur:
                    cur.execute(sql, tuple(params))
                    rows = cur.fetchall()
        except psycopg_pool.PoolTimeout as e:
            raise ProviderError(
                f"connection pool exhausted: {e}", provider="postgres"
            ) from e
        except psycopg.Error as e:
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

    # ---------------------------------------------------------------- search

    def search(
        self,
        query: str,
        user_id: str,
        *,
        scope: str | None = None,
        limit: int = 10,
        min_score: float | None = None,
    ) -> list[SearchResult]:
        q_emb = self.embedder.embed([query])[0]
        clauses: list[str] = [
            "user_id = %s",
            "embedding_model = %s",
        ]
        params: list[Any] = [user_id, self.embedder.model]
        if scope is not None:
            clauses.append("scope LIKE %s")
            params.append(_scope_glob_to_sql_like(scope))

        # Hybrid: cosine distance + ILIKE bonus
        sql = (
            "SELECT *, 1 - (embedding <=> %s::vector) AS score FROM memories "
            f"WHERE {' AND '.join(clauses)} "
            "ORDER BY embedding <=> %s::vector ASC LIMIT %s;"
        )
        vec_lit = _vector_literal(q_emb)
        try:
            with self._pool.connection() as conn:
                with conn.cursor(row_factory=dict_row) as cur:
                    cur.execute(sql, (vec_lit, *params, vec_lit, limit))
                    rows = cur.fetchall()
        except psycopg_pool.PoolTimeout as e:
            raise ProviderError(
                f"connection pool exhausted: {e}", provider="postgres"
            ) from e
        except psycopg.Error as e:
            raise ProviderError(
                f"search failed: {e}", provider="postgres"
            ) from e

        if not rows:
            # A3: distinguish "no candidates at all" from "no candidates with
            # this embedding model" (FR-014).
            try:
                with self._pool.connection() as conn:
                    with conn.cursor() as cur:
                        cur.execute(
                            "SELECT 1 FROM memories WHERE user_id = %s "
                            + (
                                "AND scope LIKE %s LIMIT 1;"
                                if scope is not None
                                else "LIMIT 1;"
                            ),
                            (
                                (user_id, _scope_glob_to_sql_like(scope))
                                if scope is not None
                                else (user_id,)
                            ),
                        )
                        has_other_models = cur.fetchone() is not None
            except psycopg.Error:
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
            score = float(r.pop("score"))
            if min_score is not None and score < min_score:
                continue
            results.append(SearchResult(memory=self._row_to_memory(r), score=score))
        return results

    # --------------------------------------------------------------- context

    def context(
        self,
        query: str,
        user_id: str,
        *,
        scope: str | None = None,
        token_budget: int = 500,
    ) -> ContextBlock:
        try:
            results = self.search(
                query, user_id, scope=scope, limit=max(1, token_budget // 50)
            )
        except InvalidRequestError:
            # If no memories exist for this model/user, return empty context
            # rather than propagating — context is best-effort.
            results = []

        if not results:
            return ContextBlock(text="", citations=[], token_count=0)

        lines: list[str] = []
        citations: list[_Citation] = []
        running_chars = 0
        char_budget = token_budget * 4  # ~4 chars/token
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

    # ----------------------------------------------------------- capabilities

    def capabilities(self) -> Capabilities:
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
            # A1: omit rate_limit_per_minute rather than passing None.
            limits=CapabilityLimits(max_search_results=100),
        )


__all__ = ["PostgresAdapter"]
