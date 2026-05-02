"""Client disconnect cancellation (T040b / contracts §9 — C-DIS-1..3).

These tests use a slow async adapter to simulate work in flight, then
abort the request via httpx and assert that the underlying coroutine
was cancelled (and any acquired resources are released).
"""

from __future__ import annotations

import asyncio

import httpx
import pytest


pytestmark = pytest.mark.asyncio


async def test_client_disconnect_cancels_route(server_factory, monkeypatch):
    """Disconnecting the client mid-request must cancel the route task."""
    app, _, mem = await server_factory("passthrough")

    cancelled = asyncio.Event()
    started = asyncio.Event()

    async def slow_capabilities():
        started.set()
        try:
            await asyncio.sleep(5.0)
        except asyncio.CancelledError:
            cancelled.set()
            raise
        # not reached
        from openmem.types import Capabilities, CapabilityFeatures

        return Capabilities(
            omp_version="0.1.0", provider="passthrough", verbs=[],
            features=CapabilityFeatures(),
        )

    # Patch on the AsyncMemory directly: capabilities() is what the route awaits.
    monkeypatch.setattr(mem, "capabilities", slow_capabilities)

    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)

    async def make_request(client):
        return await client.get("/capabilities")

    async with httpx.AsyncClient(
        transport=transport, base_url="http://test", timeout=10.0
    ) as client:
        task = asyncio.create_task(make_request(client))
        # wait until the slow handler is actually running
        await asyncio.wait_for(started.wait(), timeout=2.0)
        # cancel the in-flight request
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, httpx.RequestError):
            pass

    # The handler's cancellation should have propagated.
    await asyncio.wait_for(cancelled.wait(), timeout=2.0)
    assert cancelled.is_set()
