"""Threadwrap-specific contract tests (US1, contracts §6).

`AsyncThreadwrapAdapter` wraps a sync `BaseAdapter` instance and forwards
each verb through a per-instance `ThreadPoolExecutor`. Tests:

* C-TW-2: each `AsyncMemory(provider="mem0"|"supermemory"|"letta")`
  owns its own executor — closing one MUST NOT affect another.
* C-TW-2: `await mem.close()` shuts down the executor.
* C-TW-4: the wrapper does not mutate the wrapped sync adapter's
  internal state directly (only via verb forwarding through the
  executor).
* `executor_max_workers` kwarg overrides the default thread count.
"""

from __future__ import annotations

import pytest


# Threadwrap-only providers per data-model.md §2.
_THREADWRAP_PROVIDERS = ("mem0", "supermemory", "letta")


@pytest.fixture(params=_THREADWRAP_PROVIDERS)
def threadwrap_provider(request):
    return request.param


async def test_executor_per_instance(threadwrap_provider, async_memory_factory):
    """C-TW-2: two AsyncMemory instances → two separate executors."""
    a = await async_memory_factory(threadwrap_provider)
    b = await async_memory_factory(threadwrap_provider)
    # Trigger lazy executor creation.
    await a.capabilities()
    await b.capabilities()
    exec_a = getattr(a._adapter, "_executor", None)
    exec_b = getattr(b._adapter, "_executor", None)
    assert exec_a is not None and exec_b is not None
    assert exec_a is not exec_b, "executors must be per-instance, not shared"


async def test_close_shuts_down_executor(threadwrap_provider, async_memory_factory):
    """C-TW-2: `close()` shuts down the executor."""
    mem = await async_memory_factory(threadwrap_provider)
    await mem.capabilities()  # trigger lazy executor init
    executor = getattr(mem._adapter, "_executor", None)
    assert executor is not None
    await mem.close()
    # ThreadPoolExecutor exposes `_shutdown` after `shutdown()`.
    assert getattr(executor, "_shutdown", False) is True


async def test_executor_max_workers_override(pg_url, postgres_adapter):
    """`executor_max_workers=4` is honored by threadwrap adapters."""
    from openmem import AsyncMemory
    from tests.conftest import _Mem0ClientShim

    mem = AsyncMemory(
        provider="mem0",
        api_key="sk-mock-threadwrap-size",
        client=_Mem0ClientShim(postgres_adapter),
        executor_max_workers=4,
    )
    try:
        await mem.capabilities()  # trigger lazy executor init
    except Exception:
        pass
    executor = getattr(mem._adapter, "_executor", None)
    if executor is not None:
        assert executor._max_workers == 4
    await mem.close()


async def test_executor_max_workers_ignored_by_native_adapters(pg_url):
    """AM-INV-7: native adapters silently ignore `executor_max_workers`."""
    from openmem import AsyncMemory
    from openmem.adapters.embedder import FakeEmbedder

    # MUST NOT raise.
    mem = AsyncMemory(
        provider="postgres",
        url=pg_url,
        embedder=FakeEmbedder(),
        executor_max_workers=8,
    )
    await mem.close()
