"""Async adapter Protocol — the async mirror of `BaseAdapter`.

Every async adapter (postgres/passthrough native, threadwrap for sync-only
providers) must satisfy this Protocol structurally. The return types are
identical to the sync `BaseAdapter` so `Memory` and `AsyncMemory` agree
byte-for-byte on outputs (data-model §1 invariant AM-INV-7, SC-008).

Per Constitution Principle II, every concrete async adapter must pass the
parametrized contract suite at `sdk-python/tests/async/test_async_*.py`.
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol, runtime_checkable

from ..types import (
    AuditEntry,
    Capabilities,
    ContextBlock,
    Memory,
    MemoryInput,
    MemoryPage,
    MemoryUpdate,
    SearchResult,
)


@runtime_checkable
class AsyncBaseAdapter(Protocol):
    """Async mirror of `BaseAdapter`.

    All ten verbs are ``async def``. Return types match the sync surface
    so `Memory` (sync) and `AsyncMemory` (async) callers can swap freely.
    """

    async def add(self, memory: MemoryInput) -> Memory: ...

    async def get(self, id: str) -> Memory: ...

    async def update(self, id: str, update: MemoryUpdate) -> Memory: ...

    async def delete(self, id: str) -> None: ...

    async def list(  # noqa: A003 — match OMP verb name
        self,
        user_id: str,
        *,
        scope: str | None = None,
        tag: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        limit: int = 50,
        cursor: str | None = None,
    ) -> MemoryPage: ...

    async def search(
        self,
        query: str,
        user_id: str,
        *,
        scope: str | None = None,
        limit: int = 10,
        min_score: float | None = None,
    ) -> list[SearchResult]: ...

    async def context(
        self,
        query: str,
        user_id: str,
        *,
        scope: str | None = None,
        token_budget: int = 500,
    ) -> ContextBlock: ...

    async def capabilities(self) -> Capabilities: ...

    async def audit(
        self,
        user_id: str,
        *,
        app: str | None = None,
        since: datetime | None = None,
        limit: int = 100,
    ) -> list[AuditEntry]: ...

    async def wait_for_ingest(
        self,
        ids: list[str],
        user_id: str,
        *,
        timeout: float | None = None,
    ) -> None: ...

    async def close(self) -> None: ...


__all__ = ["AsyncBaseAdapter"]
