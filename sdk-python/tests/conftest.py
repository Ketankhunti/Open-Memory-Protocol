"""Pytest fixtures for the OMP conformance suite.

A session-scoped pgvector container backs every adapter under test. The
``adapter`` fixture is parametrized so adding a new adapter (M2:
``mem0_adapter``, ``supermemory_adapter``, etc.) requires only one new
entry in ``params`` — the contract tests themselves never change.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from typing import Any

import pytest

# Use an env var so CI without docker can fall back to a real Postgres URL.
_USE_TESTCONTAINER = os.environ.get("PG_URL") is None


@pytest.fixture(scope="session")
def pg_url() -> Iterator[str]:
    """Provide a Postgres+pgvector URL.

    Prefers ``$PG_URL`` if set (CI / local Postgres). Otherwise spins up a
    ``pgvector/pgvector:pg16`` testcontainer.
    """
    if not _USE_TESTCONTAINER:
        yield os.environ["PG_URL"]
        return

    try:
        from testcontainers.postgres import PostgresContainer  # type: ignore
    except ImportError:
        pytest.skip(
            "testcontainers not installed; set PG_URL or `pip install openmem[dev]`"
        )

    container: Any = PostgresContainer("pgvector/pgvector:pg16")
    container.start()
    try:
        yield container.get_connection_url().replace(
            "postgresql+psycopg2://", "postgresql://"
        )
    finally:
        container.stop()


@pytest.fixture(scope="module")
def postgres_adapter(pg_url: str):
    """Concrete PostgresAdapter with FakeEmbedder."""
    from openmem.adapters.embedder import FakeEmbedder
    from openmem.adapters.postgres import PostgresAdapter

    return PostgresAdapter(url=pg_url, embedder=FakeEmbedder())


@pytest.fixture(params=["postgres"])
def adapter(request, postgres_adapter):
    """Parametrized adapter fixture.

    To add a new adapter in M2:
      1. Add a fixture (e.g. ``mem0_adapter``) above.
      2. Append its name to ``params`` here and to the dispatch dict below.
    """
    dispatch = {
        "postgres": postgres_adapter,
    }
    return dispatch[request.param]


@pytest.fixture(autouse=True)
def _clean_db(request):
    """Truncate the memories table between every test that uses the adapter."""
    needs_db = any(
        name in request.fixturenames
        for name in ("adapter", "postgres_adapter", "pg_url")
    )
    if not needs_db:
        yield
        return
    pa = request.getfixturevalue("postgres_adapter")
    with pa._conn.cursor() as cur:
        cur.execute("TRUNCATE TABLE memories")
    pa._conn.commit()
    yield
