"""Request size limit (T040 / contracts §4 — C-SIZ-1/2)."""

from __future__ import annotations

import httpx
import pytest


pytestmark = pytest.mark.asyncio


async def test_oversize_with_content_length_rejected(server_factory):
    """C-SIZ-1: trust Content-Length and reject before parsing the body."""
    app, _, _ = await server_factory("passthrough", max_request_bytes=2048)
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        r = await c.post(
            "/memories",
            json={"content": "x" * 8192, "user_id": "u"},
        )
    assert r.status_code == 413
    body = r.json()
    assert body["error"]["code"] == "payload_too_large"
    assert body["error"]["type"] == "invalid"


async def test_under_limit_request_passes(server_factory):
    app, _, _ = await server_factory("passthrough", max_request_bytes=64 * 1024)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        r = await c.post(
            "/memories",
            json={"content": "small", "user_id": "u"},
        )
    assert r.status_code == 201


async def test_oversize_chunked_no_content_length(server_factory):
    """C-SIZ-2: bounded chunked read aborts at the limit."""
    app, _, _ = await server_factory("passthrough", max_request_bytes=1024)

    async def gen():
        # ~16 KiB total in 16-byte chunks; far above the 1 KiB limit.
        for _ in range(1024):
            yield b"a" * 16

    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        r = await c.post(
            "/memories",
            content=gen(),
            headers={"Content-Type": "application/json"},
        )
    assert r.status_code == 413
    assert r.json()["error"]["code"] == "payload_too_large"
