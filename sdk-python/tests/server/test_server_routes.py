"""Happy-path tests for all 9 routes (T034 / contracts §1)."""

from __future__ import annotations

import pytest


pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------- /capabilities

async def test_capabilities_ok(client_passthrough):
    r = await client_passthrough.get("/capabilities")
    assert r.status_code == 200
    body = r.json()
    assert body["omp_version"]
    assert body["provider"] == "passthrough"
    assert "verbs" in body and "features" in body


# ---------------------------------------------------------- /memories CRUD

async def test_add_memory_ok(client_passthrough):
    r = await client_passthrough.post(
        "/memories",
        json={"content": "hello world", "user_id": "u-alice"},
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["content"] == "hello world"
    assert body["user_id"] == "u-alice"
    assert body["id"]


async def test_get_memory_ok(client_passthrough):
    r = await client_passthrough.post(
        "/memories",
        json={"content": "to be fetched", "user_id": "u-bob"},
    )
    mid = r.json()["id"]
    r2 = await client_passthrough.get(
        f"/memories/{mid}", headers={"X-User-Id": "u-bob"}
    )
    assert r2.status_code == 200
    assert r2.json()["id"] == mid


async def test_update_memory_ok(client_passthrough):
    r = await client_passthrough.post(
        "/memories",
        json={"content": "v1", "user_id": "u-pat"},
    )
    mid = r.json()["id"]
    r2 = await client_passthrough.patch(
        f"/memories/{mid}", json={"content": "v2"}
    )
    assert r2.status_code == 200, r2.text
    assert r2.json()["content"] == "v2"


async def test_delete_memory_ok(client_passthrough):
    r = await client_passthrough.post(
        "/memories",
        json={"content": "doomed", "user_id": "u-eve"},
    )
    mid = r.json()["id"]
    r2 = await client_passthrough.delete(
        f"/memories/{mid}", headers={"X-User-Id": "u-eve"}
    )
    assert r2.status_code == 204
    assert r2.content == b""


async def test_list_memories_ok(client_passthrough):
    for i in range(3):
        await client_passthrough.post(
            "/memories",
            json={"content": f"item {i}", "user_id": "u-list"},
        )
    r = await client_passthrough.get("/memories", params={"user_id": "u-list"})
    assert r.status_code == 200
    body = r.json()
    assert "items" in body
    assert len(body["items"]) >= 3


# ---------------------------------------------------------- search + context

async def test_search_memories_ok(client_passthrough):
    await client_passthrough.post(
        "/memories",
        json={"content": "the quick brown fox", "user_id": "u-search"},
    )
    r = await client_passthrough.get(
        "/memories/search",
        params={"q": "fox", "user_id": "u-search"},
    )
    assert r.status_code == 200
    body = r.json()
    assert "results" in body
    assert isinstance(body["results"], list)


async def test_get_context_ok(client_passthrough):
    await client_passthrough.post(
        "/memories",
        json={"content": "context fodder", "user_id": "u-ctx"},
    )
    r = await client_passthrough.post(
        "/context",
        json={"query": "fodder", "user_id": "u-ctx", "token_budget": 100},
    )
    assert r.status_code == 200
    body = r.json()
    assert "text" in body
    assert "citations" in body


async def test_get_audit_ok(client_passthrough):
    r = await client_passthrough.get("/audit", params={"user_id": "u-audit"})
    # audit is optional per Capabilities; passthrough mock backend may
    # decline (405 unsupported_capability) but the route MUST exist.
    assert r.status_code in (200, 405)
    if r.status_code == 200:
        assert "entries" in r.json()
    else:
        assert r.json()["error"]["code"] == "unsupported_capability"


# ---------------------------------------------------------- user_id checks (C-UID-2)

async def test_add_memory_rejects_missing_user_id(client_passthrough):
    r = await client_passthrough.post(
        "/memories", json={"content": "no-user"}
    )
    assert r.status_code == 400
    body = r.json()
    assert body["error"]["code"] == "invalid_request"


async def test_add_memory_rejects_blank_user_id(client_passthrough):
    r = await client_passthrough.post(
        "/memories", json={"content": "blank", "user_id": "   "}
    )
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "invalid_request"


async def test_get_memory_rejects_missing_header(client_passthrough):
    r = await client_passthrough.get("/memories/some-id")
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "invalid_request"


async def test_list_rejects_missing_user_id(client_passthrough):
    r = await client_passthrough.get("/memories")
    # FastAPI Query(min_length=1, required=True) → RequestValidationError → 400.
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "invalid_request"


# ---------------------------------------------------------- CORS default-deny (C-CORS-1)

async def test_cors_default_deny(client_passthrough):
    """No CORSMiddleware installed by default → no Access-Control-Allow-Origin header."""
    r = await client_passthrough.get(
        "/capabilities", headers={"Origin": "https://evil.example.com"}
    )
    assert r.status_code == 200
    assert "access-control-allow-origin" not in {
        k.lower() for k in r.headers.keys()
    }


async def test_cors_enabled_when_configured(server_factory):
    import httpx

    app, _, _ = await server_factory(
        "passthrough", cors_origins=("https://app.example.com",)
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://test"
    ) as client:
        # Preflight from allowed origin → 200 + ACAO header.
        r = await client.options(
            "/capabilities",
            headers={
                "Origin": "https://app.example.com",
                "Access-Control-Request-Method": "GET",
            },
        )
        assert r.status_code == 200
        assert (
            r.headers.get("access-control-allow-origin")
            == "https://app.example.com"
        )
