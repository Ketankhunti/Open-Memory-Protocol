"""Pytest fixtures for the FastAPI server suite (M3.2 PR-B).

Every fixture builds a fresh app from `OmpServerConfig` and wires the
`AsyncMemory` lifecycle by hand (we bypass `create_app`'s lifespan
manager for tests so we get fine-grained control over startup/shutdown
errors). The `client` fixture returns an `httpx.AsyncClient` over
`httpx.ASGITransport`, which talks directly to the in-process app
without binding a real TCP socket — this keeps the suite fast and
hermetic.
"""

from __future__ import annotations

from typing import Any, AsyncIterator, Awaitable, Callable

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI

from openmem.async_memory import AsyncMemory
from openmem.server.config import OmpServerConfig

# Re-export the async-suite fixtures without `from tests.async.conftest`
# (which is a SyntaxError because `async` is reserved). Pytest discovers
# the re-exported names by attribute.
import importlib as _importlib
_async_conftest = _importlib.import_module("tests.async.conftest")
async_memory_factory = _async_conftest.async_memory_factory  # noqa: F401


# Build apps without lifespan so we can swap the in-app `AsyncMemory`
# for a controlled instance built from the parametrized factory.
def _build_app_no_lifespan(
    config: OmpServerConfig, memory: AsyncMemory
) -> FastAPI:
    from openmem.server.errors import register_exception_handlers
    from openmem.server.middleware import (
        LoggingMiddleware,
        MaxRequestSizeMiddleware,
    )
    from openmem.server.routes import health_router, router

    app = FastAPI(title="omp-server", version="0.1.0")
    app.add_middleware(LoggingMiddleware)
    app.add_middleware(
        MaxRequestSizeMiddleware, max_bytes=config.max_request_bytes
    )
    if config.cors_origins:
        from fastapi.middleware.cors import CORSMiddleware

        app.add_middleware(
            CORSMiddleware,
            allow_origins=list(config.cors_origins),
            allow_credentials=False,
            allow_methods=["GET", "POST", "PATCH", "DELETE"],
            allow_headers=["Content-Type", "X-User-Id", "X-Request-Id"],
        )
    register_exception_handlers(app)
    app.include_router(router)
    app.include_router(health_router)
    app.state.memory = memory
    app.state.config = config
    return app


@pytest_asyncio.fixture
async def server_factory(
    async_memory_factory,
) -> AsyncIterator[Callable[..., Awaitable[tuple[FastAPI, OmpServerConfig, AsyncMemory]]]]:
    """Coroutine factory that returns a `(app, config, memory)` tuple.

    Default provider is `passthrough` (in-process mock) so tests stay
    fast. Pass `provider="postgres"` (etc.) to exercise other adapters.
    """

    async def _make(
        provider: str = "passthrough",
        *,
        max_request_bytes: int = 1024 * 1024,
        cors_origins: tuple[str, ...] = (),
        **memory_overrides: Any,
    ) -> tuple[FastAPI, OmpServerConfig, AsyncMemory]:
        mem = await async_memory_factory(provider, **memory_overrides)
        # Adapter is lazy in AsyncMemory — force-init by calling
        # capabilities so the pool/client exists before the first request.
        await mem.capabilities()
        cfg = OmpServerConfig(
            provider=provider,
            host="127.0.0.1",
            port=8080,
            max_request_bytes=max_request_bytes,
            cors_origins=cors_origins,
            postgres_url="postgresql://x" if provider == "postgres" else None,
            passthrough_base_url=(
                "http://omp.test" if provider == "passthrough" else None
            ),
            mem0_api_key="sk-mock" if provider == "mem0" else None,
            supermemory_api_key="sk-mock" if provider == "supermemory" else None,
            letta_api_key="sk-mock" if provider == "letta" else None,
        )
        app = _build_app_no_lifespan(cfg, mem)
        return app, cfg, mem

    yield _make


@pytest_asyncio.fixture
async def app_passthrough(server_factory) -> FastAPI:
    """A ready-to-use FastAPI app backed by the passthrough mock provider."""
    app, _, _ = await server_factory("passthrough")
    return app


@pytest_asyncio.fixture
async def client_passthrough(
    app_passthrough: FastAPI,
) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app_passthrough)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://test"
    ) as client:
        yield client
