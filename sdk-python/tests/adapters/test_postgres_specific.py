"""Postgres-specific tests (not part of the cross-adapter contract suite)."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import pytest

from openmem.adapters.embedder import FakeEmbedder
from openmem.adapters.postgres import PostgresAdapter
from openmem.errors import InvalidRequestError
from openmem.types import MemoryInput


def test_ddl_is_idempotent(pg_url):
    a1 = PostgresAdapter(url=pg_url, embedder=FakeEmbedder())
    a2 = PostgresAdapter(url=pg_url, embedder=FakeEmbedder())
    assert a1.capabilities().provider == a2.capabilities().provider == "postgres"


def test_concurrent_inserts_do_not_deadlock(postgres_adapter):
    def _ins(i: int) -> str:
        m = postgres_adapter.add(MemoryInput(content=f"c{i}", user_id="cuser"))
        return m.id

    with ThreadPoolExecutor(max_workers=10) as ex:
        ids = list(ex.map(_ins, range(50)))
    assert len(set(ids)) == 50


def test_embedding_dimension_mismatch_raises_invalid_request(postgres_adapter):
    """I2 / EC-005: pre-INSERT dim check raises InvalidRequestError."""
    original = postgres_adapter.embedder
    try:
        postgres_adapter.embedder = FakeEmbedder(dim=128)  # mismatch vs table 64
        with pytest.raises(InvalidRequestError):
            postgres_adapter.add(MemoryInput(content="x", user_id="u1"))
    finally:
        postgres_adapter.embedder = original


def test_cross_model_search_hard_fails(postgres_adapter):
    """FR-014: query embedder model != indexed model → InvalidRequestError."""
    postgres_adapter.add(MemoryInput(content="seed", user_id="cmuser"))
    original = postgres_adapter.embedder
    try:
        postgres_adapter.embedder = FakeEmbedder(dim=64, model="other-model")
        with pytest.raises(InvalidRequestError):
            postgres_adapter.search("seed", "cmuser")
    finally:
        postgres_adapter.embedder = original
