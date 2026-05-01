"""SupermemoryAdapter mapping tests (M2.1) using httpx.MockTransport.

These tests assert the M2.1 contract / camelCase mapping in
[contracts/supermemory-mapping.md](../../../specs/003-m2-1-live/contracts/supermemory-mapping.md).
"""

from __future__ import annotations

import json
from typing import Any, Callable

import httpx
import pytest

from openmem.adapters.supermemory import DEFAULT_BASE_URL, SupermemoryAdapter
from openmem.errors import (
    InvalidRequestError,
    ProviderError,
    RateLimitedError,
    ScopeDeniedError,
    UnauthorizedError,
    UnsupportedCapabilityError,
)
from openmem.types import MemoryInput, MemoryUpdate


def _make(
    handler: Callable[[httpx.Request], httpx.Response],
    *,
    base_url: str = "http://supermemory.test",
) -> SupermemoryAdapter:
    return SupermemoryAdapter(
        api_key="sk-test",
        base_url=base_url,
        transport=httpx.MockTransport(handler),
    )


def _record(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "id": "sm_1",
        "content": "hello",
        "metadata": {"user_id": "u1"},
        "createdAt": "2026-01-01T00:00:00+00:00",
        "status": "done",
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Capabilities
# ---------------------------------------------------------------------------


def test_default_base_url_is_v3() -> None:
    assert DEFAULT_BASE_URL.endswith("/v3")


def test_capabilities_matches_table() -> None:
    adapter = _make(lambda r: httpx.Response(200, json={}))
    caps = adapter.capabilities()
    assert caps.provider == "supermemory"
    assert "update" not in caps.verbs
    assert "audit" not in caps.verbs
    assert "search" in caps.verbs


# ---------------------------------------------------------------------------
# Add — async ingestion shape
# ---------------------------------------------------------------------------


def test_add_posts_metadata_user_id_not_top_level() -> None:
    captured: dict[str, Any] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(req.content)
        captured["path"] = req.url.path
        return httpx.Response(200, json={"id": "sm_42", "status": "queued"})

    adapter = _make(handler)
    mem = adapter.add(MemoryInput(content="hello", user_id="u1", scope="coding"))
    assert captured["path"] == "/documents"
    body = captured["body"]
    # M2.1 invariant: user_id lives in metadata, not at top level.
    assert "user_id" not in body
    assert body["content"] == "hello"
    assert body["metadata"]["user_id"] == "u1"
    assert body["metadata"]["scope"] == "coding"
    # Returned Memory carries queued status + original content.
    assert mem.status == "queued"
    assert mem.content == "hello"
    assert mem.id == "sm_42"


# ---------------------------------------------------------------------------
# Update / audit — not advertised
# ---------------------------------------------------------------------------


def test_update_not_advertised_raises_unsupported_capability() -> None:
    """FR-111: update raises BEFORE any HTTP call."""
    called: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        called.append(req.url.path)
        return httpx.Response(200, json={})

    adapter = _make(handler)
    with pytest.raises(UnsupportedCapabilityError):
        adapter.update("sm_1", MemoryUpdate(content="x"))
    assert called == [], f"update issued an HTTP call: {called!r}"


def test_audit_not_advertised_raises_unsupported_capability() -> None:
    adapter = _make(lambda r: httpx.Response(200, json={}))
    with pytest.raises(UnsupportedCapabilityError):
        adapter.audit("u1")


# ---------------------------------------------------------------------------
# Error mapping
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "status, expected",
    [
        (401, UnauthorizedError),
        (403, ScopeDeniedError),
        (400, InvalidRequestError),
        (422, InvalidRequestError),
        (429, RateLimitedError),
        (500, ProviderError),
        (503, ProviderError),
    ],
)
def test_status_code_translates_to_exception(status: int, expected: type) -> None:
    """Non-404 errors propagate immediately through the get() poll."""
    adapter = _make(lambda r: httpx.Response(status, text="boom"))
    with pytest.raises(expected):
        adapter.get("sm_1")


def test_get_404_eventually_raises_ingestion_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """M2.1: persistent 404 → ProviderError(code='ingestion_timeout')."""
    monkeypatch.setenv("OMP_INGEST_TIMEOUT", "1")
    adapter = _make(lambda r: httpx.Response(404, text="not yet"))
    with pytest.raises(ProviderError) as excinfo:
        adapter.get("sm_pending")
    assert excinfo.value.code == "ingestion_timeout"
    assert excinfo.value.provider == "supermemory"


# ---------------------------------------------------------------------------
# user_id scoping
# ---------------------------------------------------------------------------


def test_search_empty_user_id_raises_before_call() -> None:
    """FR-104 / cross-user broadening defence."""
    called: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        called.append(req.url.path)
        return httpx.Response(200, json={"results": []})

    adapter = _make(handler)
    with pytest.raises(InvalidRequestError):
        adapter.search("q", "")
    assert called == []


def test_user_id_read_from_metadata_not_top_level() -> None:
    """FR-110 / EC-103 — top-level userId is provider-assigned, ignored."""

    def handler(req: httpx.Request) -> httpx.Response:
        # Top-level userId is something supermemory assigns; we ignore it.
        return httpx.Response(
            200,
            json={
                "id": "sm_2",
                "content": "x",
                "userId": "provider_assigned_id",
                "metadata": {"user_id": "actual_omp_user"},
                "createdAt": "2026-01-01T00:00:00+00:00",
                "status": "done",
            },
        )

    adapter = _make(handler)
    mem = adapter.get("sm_2")
    assert mem.user_id == "actual_omp_user"


# ---------------------------------------------------------------------------
# Scope round-trip
# ---------------------------------------------------------------------------


def test_scope_round_trips_via_metadata() -> None:
    sent_scope = "coding/preferences"

    def handler(req: httpx.Request) -> httpx.Response:
        # Echo back as a queued shape for add.
        return httpx.Response(200, json={"id": "sm_x", "status": "queued"})

    adapter = _make(handler)
    mem = adapter.add(MemoryInput(content="x", user_id="u1", scope=sent_scope))
    assert mem.status == "queued"
    # Adapter preserves the input scope on queued add.
    assert mem.scope == sent_scope


def test_embedding_model_omitted_when_provider_managed() -> None:
    captured: dict[str, Any] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(req.content)
        return httpx.Response(200, json={"id": "sm_x", "status": "queued"})

    adapter = _make(handler)
    adapter.add(MemoryInput(content="x", user_id="u1"))
    body = captured["body"]
    assert "embedding_model" not in body
    assert "embedding_model" not in body.get("metadata", {})


# ---------------------------------------------------------------------------
# Search — POST /search with chunk-shaped response
# ---------------------------------------------------------------------------


def test_search_posts_to_v3_search_with_filters() -> None:
    captured: dict[str, Any] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        captured["path"] = req.url.path
        captured["body"] = json.loads(req.content)
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "documentId": "sm_99",
                        "score": 0.85,
                        "title": "snippet",
                        "chunks": [
                            {"content": "snippet body", "score": 0.85}
                        ],
                        "metadata": {"user_id": "u1"},
                        "createdAt": "2026-01-01T00:00:00+00:00",
                    }
                ],
                "total": 1,
            },
        )

    adapter = _make(handler)
    results = adapter.search("snippet", "u1", limit=5)
    assert captured["path"] == "/search"
    body = captured["body"]
    assert body["q"] == "snippet"
    assert body["limit"] == 5
    assert body["filters"] == {"AND": [{"key": "user_id", "value": "u1"}]}
    assert len(results) == 1
    assert results[0].memory.id == "sm_99"
    assert results[0].score == pytest.approx(0.85)


def test_search_threshold_maps_min_score() -> None:
    captured: dict[str, Any] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(req.content)
        return httpx.Response(200, json={"results": []})

    adapter = _make(handler)
    adapter.search("q", "u1", min_score=0.7)
    assert captured["body"]["threshold"] == 0.7


# ---------------------------------------------------------------------------
# List — POST /documents/list with camelCase pagination
# ---------------------------------------------------------------------------


def test_list_posts_to_memories_list_with_camelcase_pagination() -> None:
    captured: dict[str, Any] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        captured["path"] = req.url.path
        captured["body"] = json.loads(req.content)
        return httpx.Response(
            200,
            json={
                "memories": [_record(id="sm_1"), _record(id="sm_2")],
                "pagination": {
                    "currentPage": 1,
                    "limit": 50,
                    "totalPages": 2,
                },
            },
        )

    adapter = _make(handler)
    page = adapter.list("u1", limit=50)
    assert captured["path"] == "/documents/list"
    body = captured["body"]
    assert body["limit"] == 50
    assert body["page"] == 1
    assert body["filters"] == {"AND": [{"key": "user_id", "value": "u1"}]}
    assert [m.id for m in page.items] == ["sm_1", "sm_2"]
    assert page.next_cursor is not None  # currentPage < totalPages


def test_list_no_more_pages_returns_none_cursor() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "memories": [_record()],
                "pagination": {"currentPage": 1, "limit": 50, "totalPages": 1},
            },
        )

    adapter = _make(handler)
    page = adapter.list("u1")
    assert page.next_cursor is None


def test_list_malformed_cursor_raises_before_http_call() -> None:
    """T046c precursor — cursor injection defence."""
    called: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        called.append(req.url.path)
        return httpx.Response(200, json={"memories": [], "pagination": {}})

    adapter = _make(handler)
    with pytest.raises(InvalidRequestError):
        adapter.list("u1", cursor="not-a-valid-cursor!@#$")
    assert called == [], f"malformed cursor leaked an HTTP call: {called!r}"


# ---------------------------------------------------------------------------
# Base URL override via env
# ---------------------------------------------------------------------------


def test_base_url_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    """FR-106 — SUPERMEMORY_BASE_URL overrides default."""
    monkeypatch.setenv("SUPERMEMORY_BASE_URL", "https://custom.example/v3")
    captured: dict[str, str] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        captured["url"] = str(req.url)
        return httpx.Response(200, json={"id": "x", "status": "queued"})

    adapter = SupermemoryAdapter(
        api_key="sk-test", transport=httpx.MockTransport(handler)
    )
    adapter.add(MemoryInput(content="x", user_id="u1"))
    assert captured["url"].startswith("https://custom.example/v3"), captured["url"]
