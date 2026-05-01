"""Pytest fixtures for the async-adapter / `AsyncMemory` test suite (M3.2 PR-A).

Mirrors the M2.1 sync pattern in :mod:`tests.conftest`:

* Reuses the session-scoped ``pg_url`` and module-scoped ``postgres_adapter``
  fixtures from the sync conftest (they auto-discover via pytest's
  parent-directory walk).
* Provides an ``async_memory_factory`` fixture — a coroutine factory
  ``_make_async_memory(provider, **kw)`` that constructs an
  :class:`openmem.AsyncMemory`, registers an async finalizer
  (``await mem.close()``), and returns the live instance.
* Provides a ``live_finalizer`` fixture that tracks ids returned by
  ``mem.add(...)`` so live-mode tests can ``await mem.delete(id)`` at
  teardown without leaking remote state.
* Provides a parametrized ``async_memory`` fixture covering all 5
  providers (postgres, passthrough, mem0, supermemory, letta) using the
  same dispatch pattern as the sync ``adapter`` fixture.

Live-mode gating mirrors :func:`tests.conftest._is_live_mode_active` —
``OMP_LIVE`` must be exactly ``"1"`` *and* the per-provider
``<PROVIDER>_API_KEY`` must be non-empty after ``.strip()``. Otherwise
the provider runs in mock mode against the in-process Postgres backend
via the existing sync mock-transport shims (passthrough, supermemory,
mem0, letta).
"""

from __future__ import annotations

import logging
import os
from typing import Any

import pytest
import pytest_asyncio

# Re-use the sync conftest helpers (loaded automatically by pytest's
# parent-directory walk) for live-mode detection.
from tests.conftest import _is_live_mode_active  # noqa: E402

_LIVE_LOG = logging.getLogger("openmem.tests.async.live_mode")


# ---------------------------------------------------------------------------
# Async-memory factory
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def async_memory_factory(
    request,
    pg_url,
    _omp_mock_server,
    postgres_adapter,
):
    """Yield a coroutine factory that builds and tracks `AsyncMemory` instances.

    Usage::

        async def test_something(async_memory_factory):
            mem = await async_memory_factory("postgres")
            ...  # finalizer runs `await mem.close()` automatically

    The factory accepts a provider name plus any keyword overrides that
    will be forwarded to :class:`openmem.AsyncMemory`. Each instance is
    registered for async teardown so tests never have to call
    ``close()`` manually.
    """
    from openmem import AsyncMemory  # lazy: requires `openmem[async]`
    from openmem.adapters.embedder import FakeEmbedder

    created: list[Any] = []

    async def _make_async_memory(provider: str, **overrides: Any):
        if provider == "postgres":
            kw: dict[str, Any] = {
                "provider": "postgres",
                "url": pg_url,
                "embedder": FakeEmbedder(),
            }
        elif provider == "passthrough":
            kw = {
                "provider": "passthrough",
                "base_url": "http://omp.test",
                "transport": _omp_mock_server,
            }
        elif provider == "mem0":
            if _is_live_mode_active("mem0"):  # pragma: no cover - live mode
                kw = {
                    "provider": "mem0",
                    "api_key": os.environ["MEM0_API_KEY"].strip(),
                }
            else:
                from tests.conftest import _Mem0ClientShim

                kw = {
                    "provider": "mem0",
                    "api_key": "sk-mock",
                    "client": _Mem0ClientShim(postgres_adapter),
                }
        elif provider == "supermemory":
            if _is_live_mode_active("supermemory"):  # pragma: no cover
                kw = {
                    "provider": "supermemory",
                    "api_key": os.environ["SUPERMEMORY_API_KEY"].strip(),
                }
            else:
                from tests.conftest import _build_supermemory_transport

                kw = {
                    "provider": "supermemory",
                    "api_key": "sk-mock",
                    "transport": _build_supermemory_transport(postgres_adapter),
                    "base_url": "http://supermemory.test",
                }
        elif provider == "letta":
            if _is_live_mode_active("letta"):  # pragma: no cover
                kw = {
                    "provider": "letta",
                    "api_key": os.environ["LETTA_API_KEY"].strip(),
                }
            else:
                from tests.conftest import _LettaClientShim

                kw = {
                    "provider": "letta",
                    "api_key": "sk-mock",
                    "client": _LettaClientShim(postgres_adapter),
                }
        else:  # pragma: no cover - guard against typos
            raise ValueError(f"unknown provider: {provider!r}")

        kw.update(overrides)
        mem = AsyncMemory(**kw)
        created.append(mem)
        return mem

    yield _make_async_memory

    # Async teardown: close every instance created during the test.
    # Errors are logged but never raised — a flaky close must not mask
    # the actual test result (mirrors the sync EC-105 policy).
    for mem in created:
        try:
            await mem.close()
        except Exception as exc:  # noqa: BLE001
            _LIVE_LOG.warning(
                "AsyncMemory.close() failed during teardown: %s",
                type(exc).__name__,
            )


# ---------------------------------------------------------------------------
# Parametrized async_memory fixture (mirror of sync `adapter`)
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture(
    params=["postgres", "passthrough", "mem0", "supermemory", "letta"]
)
async def async_memory(request, async_memory_factory):
    """Parametrized `AsyncMemory` fixture covering all 5 providers."""
    return await async_memory_factory(request.param)


# ---------------------------------------------------------------------------
# Live-mode finalizer: track ids and delete at teardown.
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def live_finalizer():
    """Track memory ids and ``await mem.delete(id)`` them at teardown.

    Live-mode contract tests should call ``live_finalizer.track(mem, id)``
    after every successful ``await mem.add(...)`` so the matching ids
    are wiped from the remote backend on test completion. Failures
    during cleanup are logged at WARNING and never raised (EC-105).
    """

    class _Tracker:
        def __init__(self) -> None:
            self._items: list[tuple[Any, str]] = []

        def track(self, mem: Any, memory_id: str) -> None:
            if memory_id:
                self._items.append((mem, memory_id))

        @property
        def items(self) -> list[tuple[Any, str]]:
            return list(self._items)

    tracker = _Tracker()
    yield tracker

    for mem, mid in tracker.items:
        try:
            await mem.delete(mid)
        except Exception as exc:  # noqa: BLE001
            _LIVE_LOG.warning(
                "live_finalizer delete(%s) failed: %s",
                mid,
                type(exc).__name__,
            )
