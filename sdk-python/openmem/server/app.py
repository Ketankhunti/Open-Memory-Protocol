"""FastAPI application factory (PR-B / T042).

`create_app(config)` builds the FastAPI app, wires middlewares + exception
handlers + routers, and arranges startup/shutdown to manage the
`AsyncMemory` lifecycle.

`build_app_from_env()` reads `OMP_*` env vars and constructs a config —
used by `uvicorn openmem.server:app`.
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from openmem.async_memory import AsyncMemory
from openmem.server.config import OmpServerConfig
from openmem.server.errors import register_exception_handlers
from openmem.server.middleware import (
    LoggingMiddleware,
    MaxRequestSizeMiddleware,
)
from openmem.server.routes import health_router, router

__all__ = ["create_app", "build_app_from_env"]


def create_app(config: OmpServerConfig) -> FastAPI:
    """Build the FastAPI app from a validated `OmpServerConfig`."""

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        # Build adapter on startup so a bad config fails the boot, not the
        # first request. AsyncMemory is the same facade exercised by tests.
        mem = AsyncMemory(provider=config.provider, **config.adapter_kwargs())
        await mem.__aenter__()
        app.state.memory = mem
        app.state.config = config
        try:
            yield
        finally:
            await mem.close()

    app = FastAPI(
        title="omp-server",
        version="0.1.0",
        description=(
            "Open Memory Protocol HTTP server. "
            "trusted-network deployment only — auth deferred."
        ),
        lifespan=lifespan,
    )

    # Order: outermost first.
    # 1. Logging (so it sees the final status code from inner middlewares).
    app.add_middleware(LoggingMiddleware)
    # 2. Size guard before any body parsing (C-SIZ-1 — must precede Pydantic).
    app.add_middleware(
        MaxRequestSizeMiddleware, max_bytes=config.max_request_bytes
    )
    # 3. CORS only if explicitly enabled (C-CORS-1: default-deny).
    if config.cors_origins:
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

    return app


def build_app_from_env() -> FastAPI:
    """Construct an app from `OMP_*` env vars.

    Recognized variables (CLI > env > default; this function only reads env):

    * `OMP_PROVIDER`              required
    * `OMP_HOST`                  default 127.0.0.1
    * `OMP_PORT`                  default 8080
    * `OMP_MAX_REQUEST_BYTES`     default 1048576
    * `OMP_CORS_ORIGINS`          comma-separated; default empty (CORS off)
    * `OMP_LOG_LEVEL`             default info
    * `OMP_POSTGRES_URL`          required if provider=postgres
    * `OMP_PASSTHROUGH_BASE_URL`  required if provider=passthrough
    * `MEM0_API_KEY` / `SUPERMEMORY_API_KEY` / `LETTA_API_KEY`
    """
    provider = os.environ.get("OMP_PROVIDER")
    if not provider:
        raise RuntimeError(
            "OMP_PROVIDER env var is required (set --provider on CLI or "
            "use openmem.server.create_app directly)"
        )
    cors_raw = os.environ.get("OMP_CORS_ORIGINS", "").strip()
    cors = tuple(c.strip() for c in cors_raw.split(",") if c.strip())
    cfg = OmpServerConfig(
        provider=provider,
        host=os.environ.get("OMP_HOST", "127.0.0.1"),
        port=int(os.environ.get("OMP_PORT", "8080")),
        max_request_bytes=int(
            os.environ.get("OMP_MAX_REQUEST_BYTES", str(1024 * 1024))
        ),
        cors_origins=cors,
        log_level=os.environ.get("OMP_LOG_LEVEL", "info"),
        postgres_url=os.environ.get("OMP_POSTGRES_URL"),
        passthrough_base_url=os.environ.get("OMP_PASSTHROUGH_BASE_URL"),
        mem0_api_key=os.environ.get("MEM0_API_KEY"),
        supermemory_api_key=os.environ.get("SUPERMEMORY_API_KEY"),
        letta_api_key=os.environ.get("LETTA_API_KEY"),
    )
    return create_app(cfg)
