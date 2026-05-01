"""US3 — Mem0Adapter mapping tests using a MagicMock SDK client."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from openmem.adapters.mem0 import Mem0Adapter
from openmem.errors import (
    InvalidRequestError,
    NotFoundError,
    ProviderError,
    RateLimitedError,
    UnauthorizedError,
    UnsupportedCapabilityError,
)
from openmem.types import MemoryInput, MemoryUpdate


def _make_adapter() -> tuple[Mem0Adapter, MagicMock]:
    client = MagicMock()
    adapter = Mem0Adapter(api_key="sk-test", client=client)
    return adapter, client


def _record(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "id": "mem_provider_1",
        "memory": "hello",
        "user_id": "u1",
        "created_at": "2026-01-01T00:00:00+00:00",
        "metadata": {},
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Mapping
# ---------------------------------------------------------------------------


def test_add_maps_inputs_per_table() -> None:
    adapter, client = _make_adapter()
    client.add.return_value = _record()
    adapter.add(MemoryInput(content="hello", user_id="u1", scope="coding"))
    args, kwargs = client.add.call_args
    assert kwargs["messages"] == [{"role": "user", "content": "hello"}]
    assert kwargs["user_id"] == "u1"
    assert kwargs["metadata"]["scope"] == "coding"


def test_capabilities_matches_table() -> None:
    adapter, _ = _make_adapter()
    caps = adapter.capabilities()
    assert caps.provider == "mem0"
    assert "audit" not in caps.verbs
    assert "search" in caps.verbs
    assert caps.features.scopes == "tags"


def test_unsupported_audit_raises_unsupported_capability() -> None:
    adapter, _ = _make_adapter()
    with pytest.raises(UnsupportedCapabilityError):
        adapter.audit("u1")


@pytest.mark.parametrize(
    "exc_name, expected",
    [
        ("AuthenticationError", UnauthorizedError),
        ("ValidationError", InvalidRequestError),
        ("RateLimitError", RateLimitedError),
        ("RuntimeError", ProviderError),
    ],
)
def test_provider_errors_translate(exc_name: str, expected: type) -> None:
    """Non-NotFound errors propagate through the get() poll immediately.

    M2.1 changed `get()` from single-shot to bounded-poll, so NotFound is
    no longer raised — it is interpreted as `still-ingesting` and the poll
    continues until budget exhaustion (covered in
    `test_get_not_found_eventually_raises_ingestion_timeout`).
    """
    adapter, client = _make_adapter()
    raised = type(exc_name, (Exception,), {})("boom")
    client.get.side_effect = raised
    with pytest.raises(expected):
        adapter.get("mem_x")


def test_get_not_found_eventually_raises_ingestion_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """M2.1: persistent 404 → ProviderError(code='ingestion_timeout')."""
    monkeypatch.setenv("OMP_INGEST_TIMEOUT", "1")
    adapter, client = _make_adapter()
    client.get.side_effect = NotFoundError("not yet", provider="mem0")
    with pytest.raises(ProviderError) as excinfo:
        adapter.get("evt_pending")
    assert excinfo.value.code == "ingestion_timeout"
    assert excinfo.value.provider == "mem0"


def test_pagination_cursor_round_trips() -> None:
    adapter, client = _make_adapter()
    client.get_all.return_value = [_record(id=f"mem_{i}") for i in range(50)]
    page = adapter.list("u1", limit=50)
    assert page.next_cursor is not None
    # Round-trip the cursor: next call should request page=2.
    client.get_all.return_value = []
    adapter.list("u1", limit=50, cursor=page.next_cursor)
    _, kwargs = client.get_all.call_args
    assert kwargs["page"] == 2


def test_scope_round_trips_via_metadata() -> None:
    adapter, client = _make_adapter()
    sent_scope = "coding/preferences"
    client.add.return_value = _record(metadata={"scope": sent_scope})
    mem = adapter.add(MemoryInput(content="x", user_id="u1", scope=sent_scope))
    _, kwargs = client.add.call_args
    assert kwargs["metadata"]["scope"] == sent_scope
    assert mem.scope == sent_scope


def test_embedding_model_omitted_when_provider_managed() -> None:
    adapter, client = _make_adapter()
    client.add.return_value = _record()
    adapter.add(MemoryInput(content="x", user_id="u1"))
    _, kwargs = client.add.call_args
    assert "embedding_model" not in kwargs
    assert "embedding_model" not in kwargs.get("metadata", {})


def test_x_mem0_extension_round_trips() -> None:
    adapter, client = _make_adapter()
    client.add.return_value = _record(metadata={"x-mem0-flag": "yes"})
    mem = adapter.add(MemoryInput(content="x", user_id="u1"))
    extras = mem.model_extra or {}
    # Either as a top-level x-* extension OR via x-mem0 stash
    assert "x-mem0-flag" in extras or "x-mem0" in extras


# ---------------------------------------------------------------------------
# M2.1 — v2 async-add response shape and event_id resolution
# ---------------------------------------------------------------------------


def test_add_v2_async_returns_queued_with_event_id() -> None:
    """v2 add returns `{event_id, status:'PENDING'}` → queued Memory."""
    adapter, client = _make_adapter()
    client.add.return_value = {"event_id": "evt-abc", "status": "PENDING"}
    mem = adapter.add(MemoryInput(content="user uses pnpm", user_id="u1"))
    assert mem.id == "evt-abc"
    assert mem.status == "queued"
    assert mem.content == "user uses pnpm"
    extras = mem.model_extra or {}
    assert extras.get("x-mem0", {}).get("event_id") == "evt-abc"
    assert extras.get("x-mem0", {}).get("original_content") == "user uses pnpm"


def test_add_v2_low_information_returns_synthetic_noop() -> None:
    """`{results: []}` (LLM filtered as trivial) → synthetic mem_noop_ id."""
    adapter, client = _make_adapter()
    client.add.return_value = {"results": []}
    mem = adapter.add(MemoryInput(content="hi", user_id="u1"))
    assert mem.id.startswith("mem_noop_")
    assert mem.status is None
    extras = mem.model_extra or {}
    assert extras.get("x-mem0", {}).get("noop") is True


def test_add_unexpected_response_type_raises_provider_error() -> None:
    adapter, client = _make_adapter()
    client.add.return_value = "not-a-dict"
    with pytest.raises(ProviderError):
        adapter.add(MemoryInput(content="x", user_id="u1"))


def test_get_resolves_event_id_to_memory_id(monkeypatch: pytest.MonkeyPatch) -> None:
    """v2 event_id from add() is resolved via get_all() then fetched by memory_id."""
    monkeypatch.setenv("OMP_INGEST_TIMEOUT", "5")
    adapter, client = _make_adapter()
    # add() registers event_id → user_id and original content
    client.add.return_value = {"event_id": "evt-1", "status": "PENDING"}
    adapter.add(MemoryInput(content="user prefers pnpm strongly", user_id="u1"))
    # get_all() returns the materialised memory with a different id
    client.get_all.return_value = {
        "results": [
            {"id": "mem-real-1", "memory": "user prefers pnpm", "user_id": "u1"},
        ]
    }
    client.get.return_value = _record(
        id="mem-real-1", memory="user prefers pnpm", user_id="u1"
    )
    fetched = adapter.get("evt-1")
    # Caller-visible id stays the event_id (M2.1 stable-identifier rule)
    assert fetched.id == "evt-1"
    assert fetched.status == "done"
    # Underlying SDK call used the resolved memory_id
    client.get.assert_called_with(memory_id="mem-real-1")


def test_get_resolve_falls_back_to_event_id_on_resolve_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When _resolve_event_id can't find a match it returns event_id verbatim."""
    monkeypatch.setenv("OMP_INGEST_TIMEOUT", "1")
    adapter, client = _make_adapter()
    client.add.return_value = {"event_id": "evt-x", "status": "PENDING"}
    adapter.add(MemoryInput(content="zz", user_id="u1"))
    # get_all keeps returning empty so resolve times out → falls back to evt-x
    client.get_all.return_value = []
    client.get.side_effect = NotFoundError("no", provider="mem0")
    with pytest.raises(ProviderError) as excinfo:
        adapter.get("evt-x")
    assert excinfo.value.code == "ingestion_timeout"


def test_block_on_add_returns_materialised_memory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`block_on_add=True` makes add() block until get() resolves."""
    monkeypatch.setenv("OMP_INGEST_TIMEOUT", "5")
    client = MagicMock()
    adapter = Mem0Adapter(api_key="sk-test", client=client, block_on_add=True)
    client.add.return_value = {"event_id": "evt-b", "status": "PENDING"}
    client.get_all.return_value = {
        "results": [{"id": "mem-b", "memory": "x payload", "user_id": "u1"}]
    }
    client.get.return_value = _record(id="mem-b", memory="x payload", user_id="u1")
    mem = adapter.add(MemoryInput(content="x payload", user_id="u1"))
    assert mem.status == "done"


def test_block_on_add_synthesises_noop_on_ingestion_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If upstream never materialises, return synthetic mem_noop_ instead of raising."""
    monkeypatch.setenv("OMP_INGEST_TIMEOUT", "1")
    client = MagicMock()
    adapter = Mem0Adapter(api_key="sk-test", client=client, block_on_add=True)
    client.add.return_value = {"event_id": "evt-noop", "status": "PENDING"}
    client.get_all.return_value = []  # never resolves
    client.get.side_effect = NotFoundError("no", provider="mem0")
    mem = adapter.add(MemoryInput(content="filtered content", user_id="u1"))
    assert mem.id.startswith("mem_noop_")
    assert mem.status is None


def test_should_block_on_add_honours_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OMP_INGEST_BLOCK", "1")
    adapter, _ = _make_adapter()
    assert adapter._should_block_on_add() is True
    monkeypatch.setenv("OMP_INGEST_BLOCK", "")
    assert adapter._should_block_on_add() is False


def test_wait_for_ingest_no_op_when_ids_empty() -> None:
    adapter, client = _make_adapter()
    adapter.wait_for_ingest([], "u1")  # must not call upstream
    client.get_all.assert_not_called()


def test_wait_for_ingest_skips_already_resolved_ids() -> None:
    """ids not produced by this adapter (or already resolved) skip polling."""
    adapter, client = _make_adapter()
    # Plain string id never seen by add() → not polled.
    adapter.wait_for_ingest(["unknown-id"], "u1")
    client.get_all.assert_not_called()


def test_wait_for_ingest_resolves_batch_in_one_poll(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Batch resolver finds N event ids from a single get_all page."""
    monkeypatch.setenv("OMP_INGEST_TIMEOUT", "5")
    adapter, client = _make_adapter()
    # Simulate two adds returning event_ids. Use distinct multi-token
    # content so substring matching unambiguously pairs each event_id
    # to its corresponding materialised record.
    for ev, content in [
        ("evt-a", "alpha sandwich recipe variant"),
        ("evt-b", "beta zeppelin maintenance schedule"),
    ]:
        client.add.return_value = {"event_id": ev, "status": "PENDING"}
        adapter.add(MemoryInput(content=content, user_id="u1"))
    # One get_all page contains both materialised records
    client.get_all.return_value = {
        "results": [
            {"id": "mem-A", "memory": "alpha sandwich recipe variant", "user_id": "u1"},
            {"id": "mem-B", "memory": "beta zeppelin maintenance schedule", "user_id": "u1"},
        ]
    }
    adapter.wait_for_ingest(["evt-a", "evt-b"], "u1")
    assert adapter._resolved_event["evt-a"] == "mem-A"
    assert adapter._resolved_event["evt-b"] == "mem-B"
    # Only ONE get_all call (batch) — proves no per-id loop
    assert client.get_all.call_count == 1


def test_wait_for_ingest_raises_ingestion_timeout_when_pending_remains(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OMP_INGEST_TIMEOUT", "1")
    adapter, client = _make_adapter()
    client.add.return_value = {"event_id": "evt-pending", "status": "PENDING"}
    adapter.add(MemoryInput(content="never materialises", user_id="u1"))
    client.get_all.return_value = []
    with pytest.raises(ProviderError) as excinfo:
        adapter.wait_for_ingest(["evt-pending"], "u1")
    assert excinfo.value.code == "ingestion_timeout"


def test_from_provider_coerces_string_tags_to_list() -> None:
    """mem0 v2 may flatten single-element list metadata to a comma-string."""
    adapter, client = _make_adapter()
    client.get.return_value = _record(metadata={"tags": "nodejs,python"})
    # Force a direct get path (no event-id resolution)
    mem = adapter.get("mem_provider_1")
    assert mem.tags == ["nodejs", "python"]


def test_from_provider_coerces_x_mem0_string_to_dict() -> None:
    """When mem0 round-trips x-mem0 as a JSON string, it's decoded back."""
    adapter, client = _make_adapter()
    client.get.return_value = _record(
        metadata={"x-mem0": '{"event_id":"e","note":"n"}'}
    )
    mem = adapter.get("mem_provider_1")
    extras = mem.model_extra or {}
    x_mem0 = extras.get("x-mem0") or {}
    assert x_mem0.get("note") == "n"


def test_from_provider_invalid_x_mem0_string_falls_back_to_empty() -> None:
    """Malformed JSON in x-mem0 doesn't crash _from_provider.

    The literal string is preserved as a top-level x-mem0 extension by
    the generic x-* loop; the JSON-decode-then-merge path no-ops on
    failure rather than raising.
    """
    adapter, client = _make_adapter()
    client.get.return_value = _record(metadata={"x-mem0": "not-json"})
    mem = adapter.get("mem_provider_1")  # must not raise
    extras = mem.model_extra or {}
    # The malformed value is preserved verbatim (extension fields are
    # opaque per Principle V); the contract is just "no crash".
    assert "x-mem0" in extras


def test_translate_error_maps_memory_not_found() -> None:
    """mem0 SDK ≥ 2.0 raises MemoryNotFoundError → NotFoundError."""
    cls = type("MemoryNotFoundError", (Exception,), {})
    err = Mem0Adapter._translate_error(cls("missing"))
    assert isinstance(err, NotFoundError)


def test_delete_translates_event_id_when_known(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """delete() should try the resolved memory_id first, falling back to event_id."""
    adapter, client = _make_adapter()
    # Pre-seed a resolved mapping so delete tries the real id first
    adapter._user_by_event["evt-d"] = "u1"
    adapter._resolved_event["evt-d"] = "mem-d"
    client.delete.return_value = None
    adapter.delete("evt-d")
    # First call should target the resolved id
    first_call_kwargs = client.delete.call_args_list[0].kwargs
    assert first_call_kwargs.get("memory_id") == "mem-d"

