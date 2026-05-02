"""Parametrized tests for the 11-row exception → HTTP mapping (T035 / contracts §3)."""

from __future__ import annotations

import pytest

from openmem.errors import (
    InvalidRequestError,
    NotFoundError,
    ProviderError,
    RateLimitedError,
    ScopeDeniedError,
    UnauthorizedError,
    UnsupportedCapabilityError,
)


pytestmark = pytest.mark.asyncio


# (exception factory, expected_status, expected_code)
_CASES = [
    pytest.param(
        lambda: NotFoundError("missing"),
        404, "not_found",
        id="NotFoundError->404",
    ),
    pytest.param(
        lambda: InvalidRequestError("bad input"),
        400, "invalid_request",
        id="InvalidRequestError->400",
    ),
    pytest.param(
        lambda: UnauthorizedError("nope"),
        401, "unauthorized",
        id="UnauthorizedError->401",
    ),
    pytest.param(
        lambda: ScopeDeniedError("forbidden"),
        403, "scope_denied",
        id="ScopeDeniedError->403",
    ),
    pytest.param(
        lambda: RateLimitedError("slow down"),
        429, "rate_limited",
        id="RateLimitedError->429",
    ),
    pytest.param(
        lambda: UnsupportedCapabilityError("nope"),
        405, "unsupported_capability",
        id="UnsupportedCapabilityError->405",
    ),
    pytest.param(
        lambda: ProviderError("timed out", code="ingestion_timeout"),
        504, "ingestion_timeout",
        id="ProviderError(ingestion_timeout)->504",
    ),
    pytest.param(
        lambda: ProviderError("upstream barfed"),
        502, "provider_error",
        id="ProviderError->502",
    ),
    pytest.param(
        lambda: RuntimeError("oops"),
        500, "internal_error",
        id="Unhandled->500",
    ),
]


@pytest.mark.parametrize("exc_factory,expected_status,expected_code", _CASES)
async def test_exception_mapping(
    server_factory, exc_factory, expected_status, expected_code
):
    """Mount a /boom route that raises and assert envelope shape + status."""
    import httpx
    from fastapi import APIRouter

    app, _, _ = await server_factory("passthrough")
    boom_router = APIRouter()

    @boom_router.get("/_boom")
    async def boom():
        raise exc_factory()

    app.include_router(boom_router)
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://test"
    ) as client:
        r = await client.get("/_boom")
    assert r.status_code == expected_status, r.text
    body = r.json()
    assert "error" in body
    assert body["error"]["code"] == expected_code
    assert "message" in body["error"]
    assert "type" in body["error"]


async def test_payload_too_large_envelope(server_factory):
    """Body > max_request_bytes → 413 payload_too_large (FR-021)."""
    import httpx

    app, _, _ = await server_factory("passthrough", max_request_bytes=1024)
    big_body = "x" * 4096
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://test"
    ) as client:
        r = await client.post(
            "/memories",
            json={"content": big_body, "user_id": "u-x"},
        )
    assert r.status_code == 413
    assert r.json()["error"]["code"] == "payload_too_large"


async def test_provider_unavailable_via_handler(server_factory):
    """Test 503 mapping for ProviderUnavailable sentinel."""
    import httpx
    from fastapi import APIRouter

    from openmem.server.errors import ProviderUnavailable

    app, _, _ = await server_factory("passthrough")
    router = APIRouter()

    @router.get("/_pool_dead")
    async def pool_dead():
        raise ProviderUnavailable("pool exhausted")

    app.include_router(router)
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://test"
    ) as client:
        r = await client.get("/_pool_dead")
    assert r.status_code == 503
    assert r.json()["error"]["code"] == "provider_unavailable"
