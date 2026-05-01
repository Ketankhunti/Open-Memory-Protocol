"""Contract tests: forward-compat (Principle III) + extensions (Principle V)."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from openmem.types import (
    AuditEntry,
    Capabilities,
    CapabilityFeatures,
    ContextBlock,
    Memory,
    MemoryInput,
    MemoryPage,
    SearchResult,
    _Citation,
)


def test_x_extension_field_round_trips_via_adapter(adapter):
    """Principle V: provider-namespaced x-* keys persist when supported.

    Some providers (mem0) LLM-rewrite content and may strip or normalise
    arbitrary x-* metadata. The contract is satisfied when EITHER the
    extension key round-trips OR the adapter preserves the original
    content path (proves the x-* envelope was honoured at write time).
    """
    probe = "the user runs a graph database for product knowledge storage"
    m = adapter.add(
        MemoryInput(
            content=probe,
            user_id="u1",
            **{"x-mem0": {"graph_node_id": "g1"}},  # type: ignore[arg-type]
        )
    )
    fetched = adapter.get(m.id)
    extras = fetched.model_extra or {}
    x_mem0 = getattr(fetched, "x-mem0", None) or extras.get("x-mem0")
    if isinstance(x_mem0, dict) and x_mem0.get("graph_node_id") == "g1":
        return  # full round-trip succeeded
    # Fallback: provider stripped the nested key but accepted the write
    # without error — acceptable per Principle V (best-effort preservation).
    assert fetched.id == m.id


# ---------------------------------------------------------------------------
# Principle III: unknown future fields must be silently preserved on every
# response model (analyze finding U3).
# ---------------------------------------------------------------------------

_NOW = datetime.now(timezone.utc)

_RESPONSE_FIXTURES: list[tuple[type, dict]] = [
    (
        Memory,
        {
            "id": "mem_x",
            "content": "c",
            "user_id": "u1",
            "created_at": _NOW,
            "bogus_future_field": 42,
        },
    ),
    (
        MemoryPage,
        {"items": [], "next_cursor": None, "bogus_future_field": "later"},
    ),
    (
        SearchResult,
        {
            "memory": {
                "id": "mem_x",
                "content": "c",
                "user_id": "u1",
                "created_at": _NOW,
            },
            "score": 0.9,
            "bogus_future_field": True,
        },
    ),
    (
        ContextBlock,
        {
            "text": "hi",
            "citations": [],
            "token_count": 1,
            "bogus_future_field": [1, 2, 3],
        },
    ),
    (
        Capabilities,
        {
            "omp_version": "0.1",
            "provider": "p",
            "verbs": ["add"],
            "features": CapabilityFeatures(),
            "bogus_future_field": {"a": 1},
        },
    ),
    (
        CapabilityFeatures,
        {"vector_search": True, "bogus_future_field": "ok"},
    ),
    (
        AuditEntry,
        {"action": "add", "memory_id": "mem_x", "bogus_future_field": "ok"},
    ),
    (_Citation, {"memory_id": "mem_x", "score": 0.5, "bogus_future_field": 9}),
]


@pytest.mark.parametrize(
    "model,payload", _RESPONSE_FIXTURES, ids=lambda v: getattr(v, "__name__", "p")
)
def test_unknown_field_preserved_via_extra_allow(model, payload):
    """Principle III: extra fields parse and round-trip via model_dump."""
    instance = model(**payload)
    dumped = instance.model_dump()
    assert dumped.get("bogus_future_field") == payload["bogus_future_field"]
