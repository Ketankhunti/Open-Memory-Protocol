"""Tests for the M2 connection pool in PostgresAdapter.

Covers FR-001 to FR-005, EC-001, EC-002, EC-009, SC-001, SC-002.
Written FIRST per Constitution Principle II (Red -> Green).
"""

from __future__ import annotations

import concurrent.futures as cf
import time

import pytest

from openmem.adapters.embedder import FakeEmbedder
from openmem.adapters.postgres import PostgresAdapter
from openmem.errors import ProviderError
from openmem.types import MemoryInput


def _make(pg_url: str, **pool_kwargs) -> PostgresAdapter:
    return PostgresAdapter(url=pg_url, embedder=FakeEmbedder(), **pool_kwargs)


def _input(content: str = "x") -> MemoryInput:
    return MemoryInput(content=content, user_id="u-pool")


def test_pool_kwargs_accepted_with_defaults(pg_url: str) -> None:
    """FR-002: instantiate without pool kwargs and verify it works."""
    adapter = _make(pg_url)
    try:
        m = adapter.add(_input("default-pool"))
        assert m.id.startswith("mem_")
    finally:
        adapter.close()


def test_no_lock_attribute_on_adapter(pg_url: str) -> None:
    """FR-003 / EC-009: the M1 RLock must be gone."""
    adapter = _make(pg_url)
    try:
        assert not hasattr(adapter, "_lock"), (
            "PostgresAdapter._lock must be removed in M2"
        )
    finally:
        adapter.close()


def test_first_call_works_without_warmup(pg_url: str) -> None:
    """EC-002: the very first verb call after instantiation must succeed."""
    adapter = _make(pg_url, pool_min_size=1, pool_max_size=5)
    try:
        m = adapter.add(_input("first-call"))
        assert m.content == "first-call"
    finally:
        adapter.close()


def test_pool_size_caps_concurrency(pg_url: str) -> None:
    """FR-001: pool size limits the live connection count."""
    adapter = _make(pg_url, pool_min_size=1, pool_max_size=3)
    try:
        # Hammer with 12 inserts; pool stat should never exceed 3 live conns.
        with cf.ThreadPoolExecutor(max_workers=12) as ex:
            futs = [ex.submit(adapter.add, _input(f"cap-{i}")) for i in range(12)]
            for f in cf.as_completed(futs):
                f.result()
        stats = adapter._pool.get_stats()
        assert stats.get("pool_size", 0) <= 3, stats
    finally:
        adapter.close()


def test_pool_exhaustion_raises_provider_error(pg_url: str) -> None:
    """FR-004 / EC-001: exhausted pool raises ProviderError, not hangs."""
    adapter = _make(pg_url, pool_min_size=1, pool_max_size=1, pool_timeout=0.5)
    try:
        # Hold the only connection in a worker so the next checkout times out.
        held = {"in": False, "release": False}

        def hold() -> None:
            with adapter._pool.connection() as _conn:
                held["in"] = True
                while not held["release"]:
                    time.sleep(0.01)

        with cf.ThreadPoolExecutor(max_workers=2) as ex:
            holder = ex.submit(hold)
            # wait for the holder to actually grab the connection
            for _ in range(100):
                if held["in"]:
                    break
                time.sleep(0.01)
            assert held["in"], "holder did not acquire connection"

            t0 = time.monotonic()
            with pytest.raises(ProviderError) as excinfo:
                adapter.add(_input("exhaust"))
            elapsed = time.monotonic() - t0
            assert "exhaust" in str(excinfo.value).lower() or "pool" in str(
                excinfo.value
            ).lower(), str(excinfo.value)
            assert elapsed < 2.0, f"call should fast-fail, took {elapsed:.2f}s"

            held["release"] = True
            holder.result()
    finally:
        adapter.close()


def test_pool_recycles_broken_connection(pg_url: str) -> None:
    """FR-005: a broken connection in the pool does not poison subsequent calls."""
    adapter = _make(pg_url, pool_min_size=1, pool_max_size=2)
    try:
        # First call to ensure pool is warm.
        m1 = adapter.add(_input("before-break"))
        assert m1.id

        # Manually break one pooled connection.
        with adapter._pool.connection() as conn:
            try:
                conn.close()
            except Exception:
                pass

        # Next call must succeed (pool recycles or opens fresh).
        m2 = adapter.add(_input("after-break"))
        assert m2.id and m2.id != m1.id
    finally:
        adapter.close()


@pytest.mark.timeout(120)
def test_pool_5x_throughput(pg_url: str) -> None:
    """SC-001 / SC-002: 200 concurrent inserts under 12s with pool_max_size=10.

    M1 RLock baseline (observed): ~60s for the same workload. The 5x bar is
    therefore wall_time < 12.0s.
    """
    adapter = _make(pg_url, pool_min_size=2, pool_max_size=10)
    try:
        N = 200
        t0 = time.monotonic()
        with cf.ThreadPoolExecutor(max_workers=20) as ex:
            futs = [ex.submit(adapter.add, _input(f"thr-{i}")) for i in range(N)]
            for f in cf.as_completed(futs):
                f.result()
        elapsed = time.monotonic() - t0
        assert elapsed < 12.0, (
            f"200 concurrent inserts took {elapsed:.2f}s; expected <12s "
            f"(M1 baseline ~60s)"
        )
    finally:
        adapter.close()
