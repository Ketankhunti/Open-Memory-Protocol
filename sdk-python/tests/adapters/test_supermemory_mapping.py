"""US3 — SupermemoryAdapter mapping tests using httpx.MockTransport."""

from __future__ import annotations

import json
from typing import Any, Callable

import httpx
import pytest

from openmem.adapters.supermemory import SupermemoryAdapter
from openmem.errors import (
    InvalidRequestError,
    NotFoundError,
    ProviderError,
    RateLimitedError,
    ScopeDeniedError,
    UnauthorizedError,
    UnsupportedCapabilityError,
)
from openmem.types import MemoryInput, MemoryUpdate


def _make(
    handler: Callable[[httpx.Request], httpx.Response],
) -> SupermemoryAdapter:
    return SupermemoryAdapter(
        api_key="sk-test",
        base_url="http://supermemory.test",
        transport=httpx.MockTransport(handler),
    )


def _record(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "id": "sm_1",
        "content": "hello",
        "user_id": "u1",
        "created_at": "2026-01-01T00:00:00+00:00",
        "metadata": {},
    }
    base.update(overrides)
    return base


def test_capabilities_matches_table() -> None:
    adapter = _make(lambda r: httpx.Response(200, json={}))
    caps = adapter.capabilities()
    assert caps.provider == "supermemory"
    assert "update" not in caps.verbs
    assert "audit" not in caps.verbs
    assert "search" in caps.verbs


def test_add_maps_inputs_per_table() -> None:
    captured: dict[str, Any] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(req.content)
        captured["path"] = req.url.path
        return httpx.Response(200, json=_record())

    adapter = _make(handler)
    adapter.add(MemoryInput(content="hello", user_id="u1", scope="coding"))
    assert captured["path"] == "/memories"
    body = captured["body"]
    assert body["content"] == "hello"
    assert body["metadata"]["scope"] == "coding"


def test_update_not_advertised_raises_unsupported_capability() -> None:
    adapter = _make(lambda r: httpx.Response(200, json={}))
    with pytest.raises(UnsupportedCapabilityError):
        adapter.update("sm_1", MemoryUpdate(content="x"))


def test_audit_not_advertised_raises_unsupported_capability() -> None:
    adapter = _make(lambda r: httpx.Response(200, json={}))
    with pytest.raises(UnsupportedCapabilityError):
        adapter.audit("u1")


@pytest.mark.parametrize(
    "status, expected",
    [
        (401, UnauthorizedError),
        (403, ScopeDeniedError),
        (404, NotFoundError),
        (400, InvalidRequestError),
        (422, InvalidRequestError),
        (429, RateLimitedError),
        (500, ProviderError),
        (503, ProviderError),
    ],
)
def test_status_code_translates_to_exception(status: int, expected: type) -> None:
    adapter = _make(lambda r: httpx.Response(status, text="boom"))
    with pytest.raises(expected):
        adapter.get("sm_1")


def test_scope_round_trips_via_metadata() -> None:
    sent_scope = "coding/preferences"

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_record(metadata={"scope": sent_scope}))

    adapter = _make(handler)
    mem = adapter.add(
        MemoryInput(content="x", user_id="u1", scope=sent_scope)
    )
    assert mem.scope == sent_scope


def test_embedding_model_omitted_when_provider_managed() -> None:
    captured: dict[str, Any] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(req.content)
        return httpx.Response(200, json=_record())

    adapter = _make(handler)
    adapter.add(MemoryInput(content="x", user_id="u1"))
    body = captured["body"]
    assert "embedding_model" not in body
    assert "embedding_model" not in body.get("metadata", {})


def test_search_threshold_maps_min_score() -> None:
    captured: dict[str, Any] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(req.content)
        return httpx.Response(200, json=[])

    adapter = _make(handler)
    adapter.search("q", "u1", min_score=0.7)
    assert captured["body"]["threshold"] == 0.7
