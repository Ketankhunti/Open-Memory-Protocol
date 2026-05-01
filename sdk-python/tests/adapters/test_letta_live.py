"""Letta live-API tests (M2.1 / Phase 5 / US3).

Auto-skipped unless `OMP_LIVE=1` (see conftest.pytest_collection_modifyitems).
"""

from __future__ import annotations

import pytest


@pytest.mark.live
def test_live_constructor_uses_api_key():
    """FR-112 / acceptance #1 — letta-client>=1.10 uses api_key=, not token=."""
    from openmem.adapters.letta import LettaAdapter

    # Constructor parity check — this MUST not raise.
    adapter = LettaAdapter(api_key="sk-test")
    assert adapter is not None


@pytest.mark.live
def test_live_add_long_text_returns_one_memory_with_all_passage_ids(letta_adapter):
    """FR-113 / EC-104 — auto-chunking surfaces ONE OMP memory whose
    x-letta.passage_ids carries every chunk id."""
    from openmem.types import MemoryInput

    long_text = "Letta auto-chunks long text into multiple passages. " * 30
    mem = letta_adapter.add(MemoryInput(content=long_text, user_id="omp_test"))
    extras = mem.model_extra or {}
    x_letta = extras.get("x-letta") or {}
    pids = x_letta.get("passage_ids") or []
    assert len(pids) >= 1
    # The OMP id must use the FIRST passage id.
    assert mem.id.endswith(f"_{pids[0]}")


@pytest.mark.live
def test_live_get_raises_unsupported_capability_without_network(letta_adapter):
    """FR-116 / acceptance #3."""
    from openmem.errors import UnsupportedCapabilityError

    with pytest.raises(UnsupportedCapabilityError):
        letta_adapter.get("mem_anything_p1")


@pytest.mark.live
def test_live_search_returns_results(letta_adapter):
    """FR-115 / acceptance #4."""
    results = letta_adapter.search("anything", "omp_test", limit=5)
    for r in results:
        assert r.memory.id.startswith("mem_")


@pytest.mark.live
def test_live_delete_removes_every_passage(letta_adapter):
    """FR-114 — delete fans out."""
    from openmem.types import MemoryInput

    long_text = "Delete fan-out test. " * 30
    mem = letta_adapter.add(MemoryInput(content=long_text, user_id="omp_test"))
    letta_adapter.delete(mem.id)
    # No exception → success. (We can't `get` to verify; FR-116.)
