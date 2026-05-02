"""Health endpoint tests (T037 / contracts §6)."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest


pytestmark = pytest.mark.asyncio


async def test_health_passthrough_ok(server_factory):
    """passthrough provider: HEAD upstream returns 2xx → 200."""
    app, cfg, mem = await server_factory("passthrough")

    # Replace the underlying httpx client's `head` with a stub returning 200.
    fake_resp = httpx.Response(200, request=httpx.Request("HEAD", "http://x"))
    mem._adapter._client.head = AsyncMock(return_value=fake_resp)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        r = await c.get("/healthz")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


async def test_health_passthrough_upstream_5xx_returns_503(server_factory):
    app, cfg, mem = await server_factory("passthrough")

    fake_resp = httpx.Response(500, request=httpx.Request("HEAD", "http://x"))
    mem._adapter._client.head = AsyncMock(return_value=fake_resp)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        r = await c.get("/healthz")
    assert r.status_code == 503
    assert r.json()["error"]["code"] == "provider_unavailable"


async def test_health_passthrough_timeout_returns_503(server_factory):
    app, cfg, mem = await server_factory("passthrough")

    async def slow_head(*a, **kw):
        await asyncio.sleep(5)
        return httpx.Response(200, request=httpx.Request("HEAD", "http://x"))

    mem._adapter._client.head = slow_head
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        r = await c.get("/healthz")
    assert r.status_code == 503


@pytest.mark.parametrize("provider", ["mem0", "supermemory", "letta"])
async def test_health_managed_provider_unconditional_200(server_factory, provider):
    """C-HEA-4: mem0/supermemory/letta → always 200 (no upstream call)."""
    app, _, _ = await server_factory(provider)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        r = await c.get("/healthz")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


async def test_health_postgres_pool_acquire_ok(server_factory):
    """C-HEA-2: postgres → acquire+release within 1s → 200."""
    app, _, _ = await server_factory("postgres")
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        r = await c.get("/healthz")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}
