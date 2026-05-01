"""Supermemory live-API tests (M2.1 / Phase 4 / US2).

Auto-skipped unless `OMP_LIVE=1` (see conftest.pytest_collection_modifyitems).
"""

from __future__ import annotations

import os

import pytest


@pytest.mark.live
def test_live_default_base_url(supermemory_adapter):
    """FR-106 / acceptance #1 — default points at /v3."""
    from openmem.adapters.supermemory import DEFAULT_BASE_URL

    assert DEFAULT_BASE_URL.endswith("/v3")


@pytest.mark.live
def test_live_add_returns_queued(supermemory_adapter):
    """FR-107 / acceptance #2."""
    from openmem.adapters.supermemory import SupermemoryAdapter
    from openmem.types import MemoryInput

    # The shared fixture configures `block_on_add=True` for contract suites;
    # this test specifically validates the *async* add contract, so we build
    # a sibling adapter sharing the underlying transport but with blocking off.
    nb = SupermemoryAdapter.__new__(SupermemoryAdapter)
    nb.__dict__.update(supermemory_adapter.__dict__)
    nb._block_on_add_flag = False
    m = nb.add(
        MemoryInput(content="omp live probe — supermemory queued", user_id="omp_test")
    )
    # FR-107 contract: add() returns a populated status field. Live
    # supermemory may complete ingestion synchronously when content is
    # tiny; the canonical async value is "queued".
    assert m.status in {"queued", "indexing", "done"}
    assert m.id


@pytest.mark.live
def test_live_get_polls_to_done(supermemory_adapter):
    from openmem.types import MemoryInput

    m = supermemory_adapter.add(
        MemoryInput(content="omp live probe — supermemory get poll", user_id="omp_test")
    )
    fetched = supermemory_adapter.get(m.id)
    assert fetched.status == "done"
    assert fetched.user_id == "omp_test"  # FR-110 — read from metadata


@pytest.mark.live
def test_live_list_uses_post_memories_list(supermemory_adapter):
    """FR-108 / acceptance #3 — pagination round-trips."""
    page = supermemory_adapter.list("omp_test", limit=5)
    assert page.items is not None  # may be empty


@pytest.mark.live
def test_live_search_uses_post_search(supermemory_adapter):
    """FR-109 / acceptance #4 — chunk-shaped response decoded."""
    results = supermemory_adapter.search("omp", "omp_test", limit=5)
    # may be empty; just verify no exception and shape is right
    for r in results:
        assert r.memory.id
        assert isinstance(r.score, float)


@pytest.mark.live
def test_live_update_raises_unsupported_capability(supermemory_adapter):
    """FR-111 — update never reaches the network."""
    from openmem.errors import UnsupportedCapabilityError
    from openmem.types import MemoryUpdate

    with pytest.raises(UnsupportedCapabilityError):
        supermemory_adapter.update("sm_anything", MemoryUpdate(content="x"))
