"""LettaAdapter mapping tests (M2.1) using a MagicMock SDK client.

Authority: [contracts/letta-mapping.md](../../../specs/003-m2-1-live/contracts/letta-mapping.md).
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from openmem.adapters.letta import LettaAdapter, _decode_id, _encode_id
from openmem.errors import (
    InvalidRequestError,
    NotFoundError,
    ProviderError,
    UnauthorizedError,
    UnsupportedCapabilityError,
)
from openmem.types import MemoryInput, MemoryUpdate


def _make_client() -> MagicMock:
    client = MagicMock()
    agent = MagicMock()
    agent.id = "agent_xyz"
    client.agents.create.return_value = agent
    return client


def _make_adapter() -> tuple[LettaAdapter, MagicMock]:
    client = _make_client()
    adapter = LettaAdapter(api_key="sk-test", client=client)
    return adapter, client


def _passage(passage_id: str = "p1", text: str = "hello") -> dict[str, Any]:
    return {
        "id": passage_id,
        "text": text,
        "created_at": "2026-01-01T00:00:00+00:00",
        "metadata": {},
    }


# ---------------------------------------------------------------------------
# id encoding
# ---------------------------------------------------------------------------


def test_id_round_trips() -> None:
    encoded = _encode_id("agent_xyz", "p1")
    assert encoded == "mem_agent_xyz_p1"
    assert _decode_id(encoded) == ("agent_xyz", "p1")


def test_decode_rejects_malformed() -> None:
    with pytest.raises(InvalidRequestError):
        _decode_id("not-a-letta-id")


# ---------------------------------------------------------------------------
# Capabilities (M2.1: NO get, NO update)
# ---------------------------------------------------------------------------


def test_capabilities_matches_table() -> None:
    adapter, _ = _make_adapter()
    caps = adapter.capabilities()
    assert caps.provider == "letta"
    assert "get" not in caps.verbs, "FR-116 — letta MUST NOT advertise get"
    assert "update" not in caps.verbs
    assert "audit" not in caps.verbs
    assert caps.features.scopes == "native"


def test_get_raises_unsupported_capability_before_any_call() -> None:
    """FR-116 — get never reaches the network."""
    adapter, client = _make_adapter()
    with pytest.raises(UnsupportedCapabilityError):
        adapter.get("mem_agent_xyz_p1")
    assert client.agents.passages.retrieve.call_count == 0


def test_update_raises_unsupported_capability_before_any_call() -> None:
    adapter, client = _make_adapter()
    with pytest.raises(UnsupportedCapabilityError):
        adapter.update("mem_agent_xyz_p1", MemoryUpdate(content="x"))
    assert client.agents.passages.create.call_count == 0


# ---------------------------------------------------------------------------
# Per-user agent caching + invalidate-on-not-found
# ---------------------------------------------------------------------------


def test_one_agent_per_user_id_cached() -> None:
    adapter, client = _make_adapter()
    client.agents.passages.create.return_value = [_passage()]
    adapter.add(MemoryInput(content="a", user_id="u1"))
    adapter.add(MemoryInput(content="b", user_id="u1"))
    adapter.add(MemoryInput(content="c", user_id="u2"))
    assert client.agents.create.call_count == 2  # one per distinct user_id


# ---------------------------------------------------------------------------
# add — list[Passage] + x-letta.passage_ids
# ---------------------------------------------------------------------------


def test_add_handles_list_of_passages_and_stashes_all_ids() -> None:
    """FR-113 / EC-104 — auto-chunked text yields multiple passages."""
    adapter, client = _make_adapter()
    client.agents.passages.create.return_value = [
        _passage(passage_id="p1", text="chunk1"),
        _passage(passage_id="p2", text="chunk2"),
        _passage(passage_id="p3", text="chunk3"),
    ]
    mem = adapter.add(MemoryInput(content="long text", user_id="u1"))
    assert mem.id == "mem_agent_xyz_p1"  # first id is canonical
    extras = mem.model_extra or {}
    x_letta = extras.get("x-letta") or {}
    assert x_letta.get("passage_ids") == ["p1", "p2", "p3"]
    assert x_letta.get("agent_id") == "agent_xyz"
    # Original content preserved (no LLM rewrite).
    assert mem.content == "long text"


def test_add_handles_single_passage_for_short_text() -> None:
    adapter, client = _make_adapter()
    client.agents.passages.create.return_value = [_passage(passage_id="p9")]
    mem = adapter.add(MemoryInput(content="short", user_id="u1"))
    assert mem.id == "mem_agent_xyz_p9"
    extras = mem.model_extra or {}
    assert extras.get("x-letta", {}).get("passage_ids") == ["p9"]


def test_add_emits_text_kwarg_not_content() -> None:
    """Letta SDK uses `text=`, not `content=`."""
    adapter, client = _make_adapter()
    client.agents.passages.create.return_value = [_passage()]
    adapter.add(MemoryInput(content="hello", user_id="u1"))
    _, kwargs = client.agents.passages.create.call_args
    assert kwargs["agent_id"] == "agent_xyz"
    assert kwargs["text"] == "hello"
    assert "content" not in kwargs


def test_add_empty_user_id_raises_before_call() -> None:
    adapter, client = _make_adapter()
    with pytest.raises(InvalidRequestError):
        adapter.add(MemoryInput(content="x", user_id=""))
    assert client.agents.create.call_count == 0


# ---------------------------------------------------------------------------
# search — top_k= (NOT limit=)
# ---------------------------------------------------------------------------


def test_search_uses_top_k_kwarg() -> None:
    """FR-115 — passages.search must be called with top_k=, not limit=."""
    adapter, client = _make_adapter()
    client.agents.passages.create.return_value = [_passage()]
    adapter.add(MemoryInput(content="seed", user_id="u1"))
    client.agents.passages.search.return_value = [
        {"id": "p1", "content": "hit", "metadata": {}}
    ]
    adapter.search("query", "u1", limit=7)
    _, kwargs = client.agents.passages.search.call_args
    assert kwargs.get("top_k") == 7
    assert "limit" not in kwargs


def test_search_handles_passage_search_response_shape() -> None:
    """PassageSearchResponse(count, results=[Result(id, content, ...)])."""
    adapter, client = _make_adapter()
    client.agents.passages.create.return_value = [_passage()]
    adapter.add(MemoryInput(content="seed", user_id="u1"))
    client.agents.passages.search.return_value = {
        "count": 1,
        "results": [
            {"id": "p99", "content": "found", "metadata": {}}
        ],
    }
    results = adapter.search("q", "u1")
    assert len(results) == 1
    assert results[0].memory.id == "mem_agent_xyz_p99"
    assert results[0].memory.content == "found"
    # Real Letta carries no score; we default to 0.0 (SearchResult requires float).
    assert results[0].score == 0.0


def test_search_empty_user_id_raises_before_call() -> None:
    adapter, client = _make_adapter()
    with pytest.raises(InvalidRequestError):
        adapter.search("q", "")
    assert client.agents.passages.search.call_count == 0


# ---------------------------------------------------------------------------
# delete — fan-out across all passage_ids
# ---------------------------------------------------------------------------


def test_delete_iterates_all_passage_ids_under_x_letta() -> None:
    """FR-114 — delete fans out across every passage id."""
    adapter, client = _make_adapter()
    client.agents.passages.create.return_value = [
        _passage(passage_id="p1"),
        _passage(passage_id="p2"),
        _passage(passage_id="p3"),
    ]
    mem = adapter.add(MemoryInput(content="long", user_id="u1"))

    adapter.delete(mem.id)
    deleted = [
        c.kwargs[adapter._delete_kwarg]
        for c in client.agents.passages.delete.call_args_list
    ]
    assert deleted == ["p1", "p2", "p3"]


def test_delete_fallback_when_not_cached() -> None:
    """When the memory wasn't created by this adapter instance, fall back
    to the parsed passage id."""
    adapter, client = _make_adapter()
    adapter.delete("mem_agent_xyz_p7")
    _, kwargs = client.agents.passages.delete.call_args
    assert kwargs[adapter._delete_kwarg] == "p7"


def test_delete_partial_failure_does_not_raise_when_at_least_one_succeeds() -> None:
    adapter, client = _make_adapter()
    client.agents.passages.create.return_value = [
        _passage(passage_id="p1"),
        _passage(passage_id="p2"),
    ]
    mem = adapter.add(MemoryInput(content="x", user_id="u1"))

    # First call succeeds, second raises a non-NotFound error.
    def _del(**kwargs):
        if kwargs[adapter._delete_kwarg] == "p2":
            raise RuntimeError("boom")

    client.agents.passages.delete.side_effect = _del
    adapter.delete(mem.id)  # MUST NOT raise


# ---------------------------------------------------------------------------
# Error mapping
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "exc_name, expected",
    [
        ("UnauthorizedError", UnauthorizedError),
        ("NotFoundError", NotFoundError),
        ("BadRequestError", InvalidRequestError),
        ("RuntimeError", ProviderError),
    ],
)
def test_provider_errors_translate(exc_name: str, expected: type) -> None:
    """Errors propagate through `add` (since `get` is unsupported now)."""
    adapter, client = _make_adapter()
    raised = type(exc_name, (Exception,), {})("boom")
    client.agents.passages.create.side_effect = raised
    with pytest.raises(expected):
        adapter.add(MemoryInput(content="x", user_id="u1"))
