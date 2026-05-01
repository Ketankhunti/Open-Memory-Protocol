"""Coverage for Postgres adapter error/edge branches without a live DB.

These tests inject a fake connection pool that raises ``psycopg.Error`` or
``psycopg_pool.PoolTimeout`` on demand so each verb's ``except`` block runs.
The adapter is constructed via ``__new__`` to bypass the live ``__init__``
(which would require a running Postgres + pgvector).
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any

import psycopg
import psycopg_pool
import pytest

from openmem.adapters.embedder import FakeEmbedder
from openmem.adapters.postgres import (
    PostgresAdapter,
    _decode_cursor,
    _encode_cursor,
    _scope_glob_to_sql_like,
    _split_extensions,
    _vector_literal,
)
from openmem.errors import (
    InvalidRequestError,
    NotFoundError,
    ProviderError,
)
from openmem.types import MemoryInput, MemoryUpdate


# ---------------------------------------------------------------------------
# Helpers — module-level pure functions
# ---------------------------------------------------------------------------


def test_vector_literal_formats_floats() -> None:
    assert _vector_literal([1, 2.5, -3.0]).startswith("[")
    assert "2.5" in _vector_literal([2.5])


def test_scope_glob_translates_star_to_percent() -> None:
    assert _scope_glob_to_sql_like("a/*") == "a/%"
    assert _scope_glob_to_sql_like(None) is None


def test_cursor_round_trip() -> None:
    ts = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
    enc = _encode_cursor(ts, "mem_xyz")
    out_ts, out_id = _decode_cursor(enc)
    assert out_ts == ts
    assert out_id == "mem_xyz"


def test_split_extensions_filters_x_keys() -> None:
    extras = _split_extensions(
        {"x-vendor": {"k": 1}, "ignored": "yes", "x-other": "v"}
    )
    assert extras == {"x-vendor": {"k": 1}, "x-other": "v"}


# ---------------------------------------------------------------------------
# Fake pool infrastructure
# ---------------------------------------------------------------------------


class _FakeCursor:
    def __init__(self, *, rowcount: int = 1, row: dict | None = None) -> None:
        self.rowcount = rowcount
        self._row = row

    def __enter__(self) -> "_FakeCursor":
        return self

    def __exit__(self, *exc: Any) -> None:
        return None

    def execute(self, *args: Any, **kwargs: Any) -> None:
        return None

    def fetchone(self) -> dict | None:
        return self._row

    def fetchall(self) -> list[dict]:
        return [self._row] if self._row else []


class _FakeConn:
    def __init__(self, cursor: _FakeCursor | None = None) -> None:
        self._cursor = cursor or _FakeCursor()
        self.committed = False
        self.rolled_back = False

    def cursor(self, *args: Any, **kwargs: Any) -> _FakeCursor:
        return self._cursor

    def commit(self) -> None:
        self.committed = True

    def rollback(self) -> None:
        self.rolled_back = True


class _FakePool:
    """Fake pool whose ``connection()`` context manager raises on demand."""

    def __init__(
        self,
        *,
        raise_pool_timeout: bool = False,
        raise_pg_error: bool = False,
        cursor: _FakeCursor | None = None,
    ) -> None:
        self.raise_pool_timeout = raise_pool_timeout
        self.raise_pg_error = raise_pg_error
        self._cursor = cursor

    @contextmanager
    def connection(self):
        if self.raise_pool_timeout:
            raise psycopg_pool.PoolTimeout("simulated pool exhaustion")
        if self.raise_pg_error:
            raise psycopg.Error("simulated psycopg failure")
        yield _FakeConn(self._cursor)

    def close(self) -> None:
        return None


def _make_adapter(pool: _FakePool, *, dim: int = 64) -> PostgresAdapter:
    a = PostgresAdapter.__new__(PostgresAdapter)
    a._url = "postgresql://fake"
    a.embedder = FakeEmbedder(dim=dim)
    a._pool = pool
    a._dim = dim
    return a


# ---------------------------------------------------------------------------
# close() is idempotent and swallows
# ---------------------------------------------------------------------------


def test_close_swallows_pool_errors() -> None:
    class _BadPool(_FakePool):
        def close(self) -> None:
            raise RuntimeError("boom")

    adapter = _make_adapter(_BadPool())
    adapter.close()  # must not raise


# ---------------------------------------------------------------------------
# add() error paths
# ---------------------------------------------------------------------------


def test_add_raises_invalid_request_on_dim_mismatch() -> None:
    adapter = _make_adapter(_FakePool(), dim=64)
    adapter.embedder = FakeEmbedder(dim=128)
    with pytest.raises(InvalidRequestError):
        adapter.add(MemoryInput(content="x", user_id="u"))


def test_add_translates_pool_timeout() -> None:
    adapter = _make_adapter(_FakePool(raise_pool_timeout=True))
    with pytest.raises(ProviderError, match="pool exhausted"):
        adapter.add(MemoryInput(content="x", user_id="u"))


def test_add_translates_psycopg_error() -> None:
    adapter = _make_adapter(_FakePool(raise_pg_error=True))
    with pytest.raises(ProviderError, match="insert failed"):
        adapter.add(MemoryInput(content="x", user_id="u"))


# ---------------------------------------------------------------------------
# get() error paths
# ---------------------------------------------------------------------------


def test_get_raises_not_found_when_row_missing() -> None:
    adapter = _make_adapter(_FakePool(cursor=_FakeCursor(row=None)))
    with pytest.raises(NotFoundError):
        adapter.get("mem_missing")


def test_get_translates_pool_timeout() -> None:
    adapter = _make_adapter(_FakePool(raise_pool_timeout=True))
    with pytest.raises(ProviderError, match="pool exhausted"):
        adapter.get("mem_x")


def test_get_translates_psycopg_error() -> None:
    adapter = _make_adapter(_FakePool(raise_pg_error=True))
    with pytest.raises(ProviderError, match="get failed"):
        adapter.get("mem_x")


# ---------------------------------------------------------------------------
# update() — empty patch, not-found, error paths
# ---------------------------------------------------------------------------


def test_update_with_empty_patch_delegates_to_get() -> None:
    """No fields set → adapter falls through to self.get(id)."""
    adapter = _make_adapter(_FakePool(cursor=_FakeCursor(row=None)))
    with pytest.raises(NotFoundError):
        adapter.update("mem_x", MemoryUpdate())


def test_update_raises_not_found_when_row_missing() -> None:
    adapter = _make_adapter(_FakePool(cursor=_FakeCursor(row=None)))
    with pytest.raises(NotFoundError):
        adapter.update("mem_x", MemoryUpdate(content="new"))


def test_update_translates_pool_timeout() -> None:
    adapter = _make_adapter(_FakePool(raise_pool_timeout=True))
    with pytest.raises(ProviderError, match="pool exhausted"):
        adapter.update("mem_x", MemoryUpdate(content="new"))


def test_update_translates_psycopg_error() -> None:
    adapter = _make_adapter(_FakePool(raise_pg_error=True))
    with pytest.raises(ProviderError, match="update failed"):
        adapter.update("mem_x", MemoryUpdate(content="new"))


def test_update_accepts_all_fields() -> None:
    """Exercise the full patch builder so each branch in update() runs."""
    row = {
        "id": "mem_x",
        "content": "new",
        "user_id": "u",
        "scope": "s",
        "tags": ["t"],
        "source": None,
        "confidence": 0.5,
        "valid_from": None,
        "valid_to": datetime.now(timezone.utc),
        "supersedes": ["mem_old"],
        "embedding_model": "fake",
        "created_at": datetime.now(timezone.utc),
        "updated_at": datetime.now(timezone.utc),
    }
    adapter = _make_adapter(_FakePool(cursor=_FakeCursor(row=row)))
    out = adapter.update(
        "mem_x",
        MemoryUpdate(
            content="new",
            scope="s",
            tags=["t"],
            confidence=0.5,
            valid_to=datetime.now(timezone.utc),
            supersedes=["mem_old"],
        ),
    )
    assert out.id == "mem_x"


# ---------------------------------------------------------------------------
# delete() error paths
# ---------------------------------------------------------------------------


def test_delete_raises_not_found_when_no_rows_affected() -> None:
    adapter = _make_adapter(_FakePool(cursor=_FakeCursor(rowcount=0)))
    with pytest.raises(NotFoundError):
        adapter.delete("mem_x")


def test_delete_translates_pool_timeout() -> None:
    adapter = _make_adapter(_FakePool(raise_pool_timeout=True))
    with pytest.raises(ProviderError, match="pool exhausted"):
        adapter.delete("mem_x")


def test_delete_translates_psycopg_error() -> None:
    adapter = _make_adapter(_FakePool(raise_pg_error=True))
    with pytest.raises(ProviderError, match="delete failed"):
        adapter.delete("mem_x")


# ---------------------------------------------------------------------------
# list() error paths and filter branches
# ---------------------------------------------------------------------------


def test_list_translates_pool_timeout() -> None:
    adapter = _make_adapter(_FakePool(raise_pool_timeout=True))
    with pytest.raises(ProviderError, match="pool exhausted"):
        adapter.list("u")


def test_list_translates_psycopg_error() -> None:
    adapter = _make_adapter(_FakePool(raise_pg_error=True))
    with pytest.raises(ProviderError, match="list failed"):
        adapter.list("u")


def test_list_applies_all_filters_and_cursor() -> None:
    """Exercise scope/tag/since/until/cursor branches in list()."""
    adapter = _make_adapter(_FakePool(cursor=_FakeCursor(row=None)))
    cursor = _encode_cursor(datetime.now(timezone.utc), "mem_z")
    page = adapter.list(
        "u",
        scope="a/*",
        tag="t",
        since=datetime.now(timezone.utc),
        until=datetime.now(timezone.utc),
        limit=5,
        cursor=cursor,
    )
    assert page.items == []
    assert page.next_cursor is None


# ---------------------------------------------------------------------------
# search() error paths
# ---------------------------------------------------------------------------


def test_search_translates_pool_timeout() -> None:
    adapter = _make_adapter(_FakePool(raise_pool_timeout=True))
    with pytest.raises(ProviderError, match="pool exhausted"):
        adapter.search("q", "u")


def test_search_translates_psycopg_error() -> None:
    adapter = _make_adapter(_FakePool(raise_pg_error=True))
    with pytest.raises(ProviderError, match="search failed"):
        adapter.search("q", "u")


def test_search_returns_empty_when_no_other_models_present() -> None:
    """No rows at all → return [] (not InvalidRequestError)."""
    adapter = _make_adapter(_FakePool(cursor=_FakeCursor(row=None)))
    assert adapter.search("q", "u") == []


def test_search_with_scope_filter_returns_empty() -> None:
    adapter = _make_adapter(_FakePool(cursor=_FakeCursor(row=None)))
    assert adapter.search("q", "u", scope="a/*") == []


def test_search_filters_by_min_score() -> None:
    """Returned row whose score < min_score is dropped."""
    row = {
        "id": "mem_x",
        "content": "c",
        "user_id": "u",
        "scope": None,
        "tags": [],
        "source": None,
        "confidence": None,
        "valid_from": None,
        "valid_to": None,
        "supersedes": [],
        "embedding_model": "fake",
        "created_at": datetime.now(timezone.utc),
        "updated_at": None,
        "score": 0.1,
    }
    adapter = _make_adapter(_FakePool(cursor=_FakeCursor(row=row)))
    assert adapter.search("q", "u", min_score=0.5) == []


# ---------------------------------------------------------------------------
# context() — InvalidRequestError fallback and empty path
# ---------------------------------------------------------------------------


def test_context_returns_empty_when_search_raises_invalid_request() -> None:
    """If search raises InvalidRequestError, context() swallows and returns empty."""

    class _BadAdapter(PostgresAdapter):
        def search(self, *a: Any, **kw: Any):  # type: ignore[override]
            raise InvalidRequestError("no rows", provider="postgres")

    adapter = _BadAdapter.__new__(_BadAdapter)
    adapter._url = "x"
    adapter.embedder = FakeEmbedder()
    adapter._pool = _FakePool()
    adapter._dim = 64
    out = adapter.context("q", "u")
    assert out.text == ""
    assert out.citations == []


def test_context_returns_empty_when_no_results() -> None:
    adapter = _make_adapter(_FakePool(cursor=_FakeCursor(row=None)))
    out = adapter.context("q", "u")
    assert out.text == ""


# ---------------------------------------------------------------------------
# capabilities()
# ---------------------------------------------------------------------------


def test_capabilities_lists_all_verbs() -> None:
    adapter = _make_adapter(_FakePool())
    caps = adapter.capabilities()
    assert caps.provider == "postgres"
    assert "search" in caps.verbs
    assert caps.features.vector_search is True
