"""US3 — LettaAdapter mapping tests using a MagicMock SDK client."""

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
# Capabilities + per-user agent caching
# ---------------------------------------------------------------------------


def test_capabilities_matches_table() -> None:
    adapter, _ = _make_adapter()
    caps = adapter.capabilities()
    assert caps.provider == "letta"
    assert "update" not in caps.verbs
    assert "audit" not in caps.verbs
    assert caps.features.temporal is True
    assert caps.features.scopes == "native"
    assert caps.features.keyword_search is False


def test_one_agent_per_user_id_cached() -> None:
    adapter, client = _make_adapter()
    client.agents.passages.create.return_value = _passage()
    adapter.add(MemoryInput(content="a", user_id="u1"))
    adapter.add(MemoryInput(content="b", user_id="u1"))
    adapter.add(MemoryInput(content="c", user_id="u2"))
    assert client.agents.create.call_count == 2  # one per distinct user_id


# ---------------------------------------------------------------------------
# Verb mapping
# ---------------------------------------------------------------------------


def test_add_emits_letta_passage_create() -> None:
    adapter, client = _make_adapter()
    client.agents.passages.create.return_value = _passage()
    mem = adapter.add(MemoryInput(content="hello", user_id="u1"))
    _, kwargs = client.agents.passages.create.call_args
    assert kwargs["agent_id"] == "agent_xyz"
    assert kwargs["text"] == "hello"
    assert mem.id == "mem_agent_xyz_p1"
    assert mem.user_id == "u1"


def test_get_uses_decoded_agent_and_passage() -> None:
    adapter, client = _make_adapter()
    client.agents.passages.retrieve.return_value = _passage(passage_id="p9")
    adapter.get("mem_agent_xyz_p9")
    _, kwargs = client.agents.passages.retrieve.call_args
    assert kwargs == {"agent_id": "agent_xyz", "passage_id": "p9"}


def test_update_not_advertised_raises_unsupported_capability() -> None:
    adapter, _ = _make_adapter()
    with pytest.raises(UnsupportedCapabilityError):
        adapter.update("mem_agent_xyz_p1", MemoryUpdate(content="x"))


# ---------------------------------------------------------------------------
# Error translation
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
    adapter, client = _make_adapter()
    raised = type(exc_name, (Exception,), {})("boom")
    client.agents.passages.retrieve.side_effect = raised
    with pytest.raises(expected):
        adapter.get("mem_agent_xyz_p1")
