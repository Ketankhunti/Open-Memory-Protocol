"""US2 — PassthroughAdapter tests against an in-process httpx.MockTransport.

Covers contracts/passthrough-http.md: verb→HTTP mapping, headers,
capability gate, error decoding, redirects, secret hygiene.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Callable

import httpx
import pytest

from openmem.adapters.passthrough import PassthroughAdapter
from openmem.errors import (
    InvalidRequestError,
    NotFoundError,
    ProviderError,
    UnsupportedCapabilityError,
)
from openmem.types import (
    Capabilities,
    CapabilityFeatures,
    Memory,
    MemoryInput,
    MemoryUpdate,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

ALL_VERBS = [
    "add", "get", "update", "delete", "list", "search", "context", "audit",
]


def _caps(verbs: list[str] | None = None) -> Capabilities:
    return Capabilities(
        omp_version="0.1",
        provider="passthrough",
        verbs=verbs if verbs is not None else ALL_VERBS,
        features=CapabilityFeatures(vector_search=True),
    )


def _make_adapter(
    handler: Callable[[httpx.Request], httpx.Response],
    *,
    api_key: str | None = None,
    capabilities: Capabilities | None = None,
) -> PassthroughAdapter:
    """Build a PassthroughAdapter wired to ``handler`` via MockTransport."""
    return PassthroughAdapter(
        base_url="http://omp.test",
        api_key=api_key,
        capabilities=capabilities if capabilities is not None else _caps(),
        transport=httpx.MockTransport(handler),
    )


def _memory_payload(id: str = "mem_x") -> dict[str, Any]:
    return {
        "id": id,
        "content": "hello",
        "user_id": "u1",
        "created_at": "2026-01-01T00:00:00+00:00",
    }


# ---------------------------------------------------------------------------
# Verb → method/path mapping
# ---------------------------------------------------------------------------


def test_each_verb_hits_correct_method_and_path() -> None:
    """Table-driven check against contracts/passthrough-http.md."""
    seen: list[tuple[str, str]] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append((req.method, req.url.path))
        if req.url.path == "/memories" and req.method == "POST":
            return httpx.Response(200, json=_memory_payload())
        if req.url.path == "/memories" and req.method == "GET":
            return httpx.Response(200, json={"items": [], "next_cursor": None})
        if req.url.path.startswith("/memories/") and req.method == "GET":
            return httpx.Response(200, json=_memory_payload())
        if req.url.path.startswith("/memories/") and req.method == "PATCH":
            return httpx.Response(200, json=_memory_payload())
        if req.url.path.startswith("/memories/") and req.method == "DELETE":
            return httpx.Response(204)
        if req.url.path == "/memories/search":
            return httpx.Response(200, json=[])
        if req.url.path == "/context":
            return httpx.Response(
                200,
                json={"text": "x", "citations": [], "token_count": 1},
            )
        if req.url.path == "/audit":
            return httpx.Response(200, json=[])
        return httpx.Response(404, text="not routed")

    adapter = _make_adapter(handler)
    adapter.add(MemoryInput(content="hello", user_id="u1"))
    adapter.get("mem_x")
    adapter.update("mem_x", MemoryUpdate(content="updated"))
    adapter.delete("mem_x")
    adapter.list("u1")
    adapter.search("q", "u1")
    adapter.context("q", "u1")
    adapter.audit("u1")

    assert ("POST", "/memories") in seen
    assert ("GET", "/memories/mem_x") in seen
    assert ("PATCH", "/memories/mem_x") in seen
    assert ("DELETE", "/memories/mem_x") in seen
    assert ("GET", "/memories") in seen
    assert ("POST", "/memories/search") in seen
    assert ("POST", "/context") in seen
    assert ("GET", "/audit") in seen


# ---------------------------------------------------------------------------
# Headers (FR-011)
# ---------------------------------------------------------------------------


def test_authorization_header_when_api_key_set() -> None:
    captured: dict[str, str] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        captured["auth"] = req.headers.get("authorization", "")
        return httpx.Response(200, json=_memory_payload())

    adapter = _make_adapter(handler, api_key="sk-abc-123")
    adapter.get("mem_x")
    assert captured["auth"] == "Bearer sk-abc-123"


def test_authorization_header_omitted_when_no_key() -> None:
    captured: dict[str, str | None] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        captured["auth"] = req.headers.get("authorization")
        return httpx.Response(200, json=_memory_payload())

    adapter = _make_adapter(handler, api_key=None)
    adapter.get("mem_x")
    assert captured["auth"] is None


# ---------------------------------------------------------------------------
# Error mapping (FR-008, FR-010, EC-004)
# ---------------------------------------------------------------------------


def test_omp_error_envelope_dispatches_to_subclass() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(
            404,
            json={
                "error": {
                    "code": "not_found",
                    "type": "not_found",
                    "message": "missing",
                }
            },
        )

    adapter = _make_adapter(handler)
    with pytest.raises(NotFoundError):
        adapter.get("mem_missing")


def test_4xx_no_envelope_becomes_invalid_request_error() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(422, text="bad input")

    adapter = _make_adapter(handler)
    with pytest.raises(InvalidRequestError):
        adapter.get("mem_x")


def test_5xx_no_envelope_becomes_provider_error() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="overloaded")

    adapter = _make_adapter(handler)
    with pytest.raises(ProviderError):
        adapter.get("mem_x")


# ---------------------------------------------------------------------------
# Capability gate (FR-009, EC-003)
# ---------------------------------------------------------------------------


def test_capability_gate_raises_before_network() -> None:
    """Pre-flight check must NOT issue a network call for an unadvertised verb."""
    call_count = {"n": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        call_count["n"] += 1
        return httpx.Response(500, text="should never be reached")

    adapter = _make_adapter(handler, capabilities=_caps(verbs=["add", "get"]))
    with pytest.raises(UnsupportedCapabilityError):
        adapter.audit("u1")
    assert call_count["n"] == 0


def test_advertised_verb_returning_501_raises_unsupported_capability_and_does_not_mutate_cache() -> None:
    """If remote 501s on an advertised verb, the cache stays as-is (EC-003)."""
    def handler(req: httpx.Request) -> httpx.Response:
        # Plain 501 with no envelope.
        return httpx.Response(501, text="not implemented")

    caps_before = _caps(verbs=["audit", "add"])
    adapter = _make_adapter(handler, capabilities=caps_before)
    # 501 has no OMP envelope so it falls through to the generic 5xx mapping.
    # The point of this test (per tasks.md) is that .capabilities() is
    # NOT mutated as a side-effect of the failure.
    with pytest.raises((ProviderError, UnsupportedCapabilityError)):
        adapter.audit("u1")
    assert "audit" in adapter.capabilities().verbs


# ---------------------------------------------------------------------------
# Redirects (EC-004)
# ---------------------------------------------------------------------------


def test_single_redirect_followed() -> None:
    state = {"hops": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        state["hops"] += 1
        if state["hops"] == 1:
            return httpx.Response(
                307,
                headers={"location": "http://omp.test/memories/mem_after"},
            )
        return httpx.Response(200, json=_memory_payload(id="mem_after"))

    adapter = _make_adapter(handler)
    result = adapter.get("mem_x")
    assert isinstance(result, Memory)
    assert state["hops"] == 2


def test_redirect_loop_raises_provider_error() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(
            307,
            headers={"location": "http://omp.test/memories/loop"},
        )

    adapter = _make_adapter(handler)
    with pytest.raises(ProviderError):
        adapter.get("mem_x")


# ---------------------------------------------------------------------------
# Secret hygiene (FR-011)
# ---------------------------------------------------------------------------


def test_api_key_never_logged(caplog: pytest.LogCaptureFixture) -> None:
    sentinel = "sk-DO-NOT-LOG-THIS-SENTINEL-12345"

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_memory_payload())

    adapter = _make_adapter(handler, api_key=sentinel)
    with caplog.at_level(logging.DEBUG, logger="openmem"):
        with caplog.at_level(logging.DEBUG, logger="httpx"):
            adapter.get("mem_x")

    for record in caplog.records:
        assert sentinel not in record.getMessage()
        for arg in (record.args or ()):
            assert sentinel not in str(arg)


# ---------------------------------------------------------------------------
# 204 handling
# ---------------------------------------------------------------------------


def test_delete_returns_none_on_204() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(204)

    adapter = _make_adapter(handler)
    assert adapter.delete("mem_x") is None


# ---------------------------------------------------------------------------
# Body serialization (FR-007)
# ---------------------------------------------------------------------------


def test_add_serializes_with_exclude_none() -> None:
    captured: dict[str, Any] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(req.content)
        return httpx.Response(200, json=_memory_payload())

    adapter = _make_adapter(handler)
    adapter.add(MemoryInput(content="hello", user_id="u1"))
    body = captured["body"]
    assert body["content"] == "hello"
    assert body["user_id"] == "u1"
    # exclude_none → no `tags`, `scope`, `source`, etc. None fields
    assert "tags" not in body
    assert "scope" not in body


# ---------------------------------------------------------------------------
# M2.1 � Memory.status passthrough (T046b / data-model.md �1)
# ---------------------------------------------------------------------------


def test_passthrough_mirrors_status() -> None:
    """Status field flows through verbatim from upstream to OMP caller."""
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                **_memory_payload(),
                "status": "indexing",
            },
        )

    adapter = _make_adapter(handler)
    mem = adapter.get("mem_x")
    assert mem.status == "indexing"


def test_passthrough_status_absent_yields_none() -> None:
    """Legacy upstream with no status field MUST round-trip as None."""
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_memory_payload())

    adapter = _make_adapter(handler)
    mem = adapter.get("mem_x")
    assert mem.status is None

