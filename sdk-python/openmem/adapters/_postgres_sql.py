"""Shared postgres SQL fragments and helpers (T007 / M3.2).

Extracted from ``openmem.adapters.postgres`` so the upcoming
``AsyncPostgresAdapter`` (T015, asyncpg-based) can reuse the **exact same**
SQL strings as the sync adapter — eliminating any drift between the two
backends and keeping `Memory` byte-identical with `AsyncMemory` (SC-008).

Behavior **must not change** — every existing sync test must still pass.
"""

from __future__ import annotations

import base64
from datetime import datetime
from typing import Any

from ulid import ULID

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

STD_FIELDS = {
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

# Static query templates (psycopg / %s placeholders).

GET_MEMORY_SQL = "SELECT * FROM memories WHERE id = %s;"

DELETE_MEMORY_SQL = "DELETE FROM memories WHERE id = %s;"

INSERT_MEMORY_SQL = """
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
"""

CREATE_EXTENSION_SQL = "CREATE EXTENSION IF NOT EXISTS vector;"

INDEX_USER_SCOPE_SQL = (
    "CREATE INDEX IF NOT EXISTS idx_memories_user_scope ON memories(user_id, scope);"
)

INDEX_TAGS_SQL = (
    "CREATE INDEX IF NOT EXISTS idx_memories_tags ON memories USING GIN(tags);"
)

INDEX_CREATED_AT_SQL = (
    "CREATE INDEX IF NOT EXISTS idx_memories_created_at "
    "ON memories(created_at DESC, id DESC);"
)


def make_create_table_sql(dim: int) -> str:
    """Return the CREATE TABLE statement parameterized by embedding dim."""
    return f"""
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
    embedding       VECTOR({dim}),
    extensions      JSONB,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ
);
"""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def new_id() -> str:
    """Generate a new ULID-suffixed memory id."""
    return f"mem_{ULID()}"


def vector_literal(vec: list[float]) -> str:
    """Render a Python list as a pgvector literal."""
    return "[" + ",".join(repr(float(x)) for x in vec) + "]"


def scope_glob_to_sql_like(scope: str | None) -> str | None:
    """Translate an OMP scope glob (``*``) to SQL ``LIKE`` (``%``)."""
    if scope is None:
        return None
    return scope.replace("*", "%")


def encode_cursor(created_at: datetime, id_: str) -> str:
    """Encode a ``(created_at, id)`` pair as a stable opaque cursor."""
    raw = f"{created_at.isoformat()}|{id_}".encode()
    return base64.urlsafe_b64encode(raw).decode("ascii")


def decode_cursor(cursor: str) -> tuple[datetime, str]:
    """Inverse of :func:`encode_cursor`."""
    raw = base64.urlsafe_b64decode(cursor.encode("ascii")).decode()
    ts, id_ = raw.split("|", 1)
    return datetime.fromisoformat(ts), id_


def split_extensions(extra: dict[str, Any]) -> dict[str, Any]:
    """Extract ``x-<provider>`` keys from a free-form dict (Principle V)."""
    return {k: v for k, v in extra.items() if k.startswith("x-")}


__all__ = [
    "STD_FIELDS",
    "GET_MEMORY_SQL",
    "DELETE_MEMORY_SQL",
    "INSERT_MEMORY_SQL",
    "CREATE_EXTENSION_SQL",
    "INDEX_USER_SCOPE_SQL",
    "INDEX_TAGS_SQL",
    "INDEX_CREATED_AT_SQL",
    "make_create_table_sql",
    "new_id",
    "vector_literal",
    "scope_glob_to_sql_like",
    "encode_cursor",
    "decode_cursor",
    "split_extensions",
]
