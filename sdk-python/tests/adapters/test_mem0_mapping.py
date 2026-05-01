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
