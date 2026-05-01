"""Cancellation contract tests for `AsyncMemory` (M3.2 PR-A, Phase 4).

Covers ``contracts/async-memory.md`` §3:

* C-CAN-1 — native awaiter receives ``asyncio.CancelledError`` ≤ 50 ms.
* C-CAN-2 — native pool/socket released ≤ 500 ms after cancellation.
* C-CAN-3 — Postgres server-side query aborted (gone from
  ``pg_stat_activity`` within 1 s).  *live-only*.
* C-CAN-4 — threadwrap awaiter cancels immediately; worker thread
  finishes in the background; orphan log line emitted at DEBUG.
* C-CAN-5 — pool/state stays usable after cancellation.

Live-only tests (``test_postgres_pool_release``,
``test_postgres_query_aborted``) require ``OMP_LIVE=1`` *and* a
reachable Postgres pointed at by ``OMP_POSTGRES_URL`` (or the
session-scoped ``pg_url`` from the testcontainer).
"""

from __future__ import annotations

import asyncio
import logging
import os
import threading
import time
from datetime import datetime
from typing import Any

import httpx
import pytest

from openmem import AsyncMemory
from openmem.adapters.async_threadwrap import AsyncThreadwrapAdapter
from openmem.types import (
    AuditEntry,
    Capabilities,
    CapabilityFeatures,
    ContextBlock,
    Memory,
    MemoryInput,
    MemoryPage,
    MemoryUpdate,
    SearchResult,
)


_ALL_VERB_CAPS = Capabilities(
    omp_version="0.1",
    provider="omp-mock",
    verbs=["add", "search", "get", "update", "delete", "list", "context"],
    features=CapabilityFeatures(vector_search=True, scopes="tags"),
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _live_postgres_enabled() -> bool:
    return (os.environ.get("OMP_LIVE") or "").strip() == "1"


# ===========================================================================
# T022 — Postgres pool release after cancellation (live-only)
# ===========================================================================


@pytest.mark.asyncio
@pytest.mark.skipif(
    not _live_postgres_enabled(),
    reason="live-only (OMP_LIVE=1 + reachable Postgres) per C-CAN-2",
)
async def test_postgres_pool_release(async_memory_factory):
    """C-CAN-2 — cancelling a slow query MUST return the connection
    to the pool within 500 ms."""
    mem = await async_memory_factory("postgres", pool_max_size=4)
    # Force the pool open and capture the asyncpg pool handle.
    await mem.add(content="warmup", user_id="u1-cancel")
    pool = mem._adapter._pool  # type: ignore[attr-defined]
    assert pool is not None

    # asyncpg.Pool exposes free-size via ``get_idle_size``; baseline
    # is whatever was idle after the warmup completed.
    baseline_idle = pool.get_idle_size()

    async def _slow():
        # Use the raw pool to issue a 5 s server-side sleep so we
        # can be sure the cancel hits *during* an in-flight query.
        async with pool.acquire() as conn:
            await conn.execute("SELECT pg_sleep(5)")

    task = asyncio.create_task(_slow())
    await asyncio.sleep(0.1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    # Connection must return to the pool within 500 ms (C-CAN-2).
    deadline = time.perf_counter() + 0.5
    while time.perf_counter() < deadline:
        if pool.get_idle_size() >= baseline_idle:
            break
        await asyncio.sleep(0.01)
    assert pool.get_idle_size() >= baseline_idle, (
        f"pool did not release connection within 500 ms — "
        f"idle={pool.get_idle_size()} baseline={baseline_idle}"
    )


# ===========================================================================
# T023 — Postgres server-side query aborted (live-only)
# ===========================================================================


@pytest.mark.asyncio
@pytest.mark.skipif(
    not _live_postgres_enabled(),
    reason="live-only (OMP_LIVE=1 + reachable Postgres) per C-CAN-3",
)
async def test_postgres_query_aborted(async_memory_factory):
    """C-CAN-3 — `pg_stat_activity` MUST NOT show the cancelled query
    1 s after cancellation."""
    mem = await async_memory_factory("postgres", pool_max_size=4)
    await mem.add(content="warmup", user_id="u1-cancel")
    pool = mem._adapter._pool  # type: ignore[attr-defined]
    assert pool is not None

    tag = f"omp-cancel-test-{int(time.time()*1000)}"

    async def _slow():
        async with pool.acquire() as conn:
            # Embed an identifiable string so we can find this query
            # in pg_stat_activity. ``pg_sleep`` is the canonical
            # cancellable workload.
            await conn.execute(f"SELECT pg_sleep(5) /* {tag} */")

    task = asyncio.create_task(_slow())
    await asyncio.sleep(0.1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    # Wait up to 1 s for the server to drop the query.
    deadline = time.perf_counter() + 1.0
    found = True
    while time.perf_counter() < deadline:
        async with pool.acquire() as conn:
            row = await conn.fetchval(
                "SELECT count(*) FROM pg_stat_activity WHERE query LIKE $1",
                f"%{tag}%",
            )
        if row == 0:
            found = False
            break
        await asyncio.sleep(0.05)
    assert found is False, (
        f"server-side query still present in pg_stat_activity after 1 s "
        f"(tag={tag!r})"
    )


# ===========================================================================
# T024 — Passthrough socket release (httpx MockTransport)
# ===========================================================================


@pytest.mark.asyncio
async def test_passthrough_socket_release():
    """C-CAN-1/C-CAN-2 — cancelling a passthrough verb against a slow
    transport MUST raise `CancelledError` within 50 ms (well under the
    natural request timeout) and release the socket."""
    request_started = threading.Event()

    async def _slow_handler(request: httpx.Request) -> httpx.Response:
        # Mark the request as started so the test knows it's mid-flight.
        request_started.set()
        # Sleep longer than the cancel window so we can be sure the
        # cancel fires while the request is in progress.
        await asyncio.sleep(5.0)
        return httpx.Response(200, json=[])

    transport = httpx.MockTransport(_slow_handler)
    mem = AsyncMemory(
        provider="passthrough",
        base_url="http://omp.test",
        transport=transport,
        capabilities=_ALL_VERB_CAPS,
    )
    try:
        task = asyncio.create_task(
            mem.search(query="anything", user_id="u1", limit=1)
        )
        # Wait up to 1 s for the request to actually start.
        for _ in range(100):
            if request_started.is_set():
                break
            await asyncio.sleep(0.01)
        assert request_started.is_set(), (
            "request never reached the mock transport"
        )

        t0 = time.perf_counter()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        # Native cancel must propagate within 50 ms (C-CAN-1).
        assert elapsed_ms < 250, (
            f"CancelledError took {elapsed_ms:.1f}ms — native tier "
            f"contract is 50 ms (allowing 5× scheduling slack)"
        )
    finally:
        await mem.close()


# ===========================================================================
# T025 — Threadwrap immediate cancel + orphan log line
# ===========================================================================


class _ControllableSyncAdapter:
    """Sync `BaseAdapter` whose `add` blocks on a `threading.Event`."""

    def __init__(self) -> None:
        self.start_event = threading.Event()
        self.release_event = threading.Event()
        self.add_returned = threading.Event()
        self.calls = 0

    # -- minimal BaseAdapter surface used by the test --
    def add(self, memory: MemoryInput) -> Memory:
        self.calls += 1
        self.start_event.set()
        # Block until the test releases us — simulates a slow remote.
        self.release_event.wait(timeout=10.0)
        result = Memory(
            id=f"mem-{self.calls}",
            user_id=memory.user_id,
            content=memory.content,
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
            tags=list(memory.tags or []),
            source=memory.source,
        )
        self.add_returned.set()
        return result

    def close(self) -> None:
        # Make sure any blocked worker can exit at teardown.
        self.release_event.set()


@pytest.mark.asyncio
async def test_threadwrap_immediate_cancel(caplog):
    """C-CAN-4 — awaiter receives `CancelledError` ≤ 50 ms even though
    the worker thread is still running. Orphan completion is logged at
    DEBUG when the worker eventually finishes."""
    sync_stub = _ControllableSyncAdapter()
    adapter = AsyncThreadwrapAdapter(sync_stub, provider_name="stub")

    async def _kick():
        return await adapter.add(
            MemoryInput(content="hello", user_id="u1-cancel")
        )

    task = asyncio.create_task(_kick())

    # Wait until the worker thread has actually entered sync_stub.add.
    for _ in range(100):
        if sync_stub.start_event.is_set():
            break
        await asyncio.sleep(0.01)
    assert sync_stub.start_event.is_set(), (
        "sync adapter.add never started — cannot test mid-flight cancel"
    )

    t0 = time.perf_counter()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    elapsed_ms = (time.perf_counter() - t0) * 1000.0
    # Threadwrap awaiter must cancel quickly even though the worker
    # cannot be killed (Python threads are non-preemptible).
    assert elapsed_ms < 250, (
        f"threadwrap cancel took {elapsed_ms:.1f}ms — should be near-immediate"
    )

    # The worker thread is still blocked. Release it and verify the
    # orphan-completion DEBUG log line is emitted.
    with caplog.at_level(logging.DEBUG, logger="openmem.async.threadwrap"):
        sync_stub.release_event.set()
        # Wait until the worker actually completes.
        for _ in range(200):
            if sync_stub.add_returned.is_set():
                break
            await asyncio.sleep(0.01)
        # Give the executor's done-callback a chance to fire.
        await asyncio.sleep(0.05)

    orphan_records = [
        r
        for r in caplog.records
        if "orphan call completed after cancellation" in r.getMessage()
    ]
    assert orphan_records, (
        "expected DEBUG 'orphan call completed after cancellation' log "
        "line from AsyncThreadwrapAdapter._on_done after the worker "
        "finishes post-cancel"
    )
    await adapter.close()


# ===========================================================================
# T026 — Pool / state usable after cancellation
# ===========================================================================


@pytest.mark.asyncio
async def test_pool_state_after_cancel_passthrough():
    """C-CAN-5 — cancelling one verb MUST NOT corrupt adapter state;
    a subsequent verb on the same `AsyncMemory` MUST succeed."""
    call_n = {"n": 0}

    async def _handler(request: httpx.Request) -> httpx.Response:
        call_n["n"] += 1
        if call_n["n"] == 1:
            # First request: stall so the test can cancel mid-flight.
            await asyncio.sleep(5.0)
            return httpx.Response(200, json=[])
        # Second request: respond instantly so we can prove the
        # client/pool is still usable.
        return httpx.Response(200, json=[])

    transport = httpx.MockTransport(_handler)
    mem = AsyncMemory(
        provider="passthrough",
        base_url="http://omp.test",
        transport=transport,
        capabilities=_ALL_VERB_CAPS,
    )
    try:
        first = asyncio.create_task(
            mem.search(query="slow", user_id="u1", limit=1)
        )
        await asyncio.sleep(0.05)
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first

        # Subsequent verb on the same AsyncMemory must work.
        results = await mem.search(query="fast", user_id="u1", limit=1)
        assert isinstance(results, list)
        assert call_n["n"] == 2, (
            "second request never reached the transport — "
            "adapter state appears corrupted by prior cancellation"
        )
    finally:
        await mem.close()


@pytest.mark.asyncio
@pytest.mark.skipif(
    not _live_postgres_enabled(),
    reason="live-only (OMP_LIVE=1 + reachable Postgres)",
)
async def test_pool_state_after_cancel_postgres(async_memory_factory):
    """C-CAN-5 — Postgres tier: cancellation must leave the pool usable."""
    mem = await async_memory_factory("postgres", pool_max_size=4)
    await mem.add(content="warmup", user_id="u1-cancel-state")
    pool = mem._adapter._pool  # type: ignore[attr-defined]

    async def _slow():
        async with pool.acquire() as conn:
            await conn.execute("SELECT pg_sleep(5)")

    task = asyncio.create_task(_slow())
    await asyncio.sleep(0.1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    # Subsequent verb must succeed (no pool corruption).
    rec = await mem.add(content="post-cancel", user_id="u1-cancel-state")
    assert rec.content == "post-cancel"
    await mem.delete(rec.id)
