"""Async threadwrap adapter (T017 / M3.2).

Wraps a sync :class:`openmem.adapters.base.BaseAdapter` instance and
forwards every verb through a per-instance
:class:`concurrent.futures.ThreadPoolExecutor`. Used for providers
without a native async client (mem0, supermemory, letta).

Cancellation contract (contracts/async-memory.md §3, *Best-effort* tier):

* The awaiter receives ``asyncio.CancelledError`` immediately when
  cancelled (C-CAN-4) — Python's ``loop.run_in_executor`` already
  honours that contract for the caller.
* The worker thread keeps running to completion (Python threads are not
  forcibly killable). When the orphaned call eventually completes, an
  ``add_done_callback`` logs at ``logging.DEBUG`` for visibility (T029,
  not a hard requirement).
"""

from __future__ import annotations

import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from functools import partial
from typing import Any

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
from .base import BaseAdapter

__all__ = ["AsyncThreadwrapAdapter"]

_LOG = logging.getLogger("openmem.async.threadwrap")


class AsyncThreadwrapAdapter:
    """Async facade over a sync `BaseAdapter` via a thread pool."""

    def __init__(
        self,
        sync_adapter: BaseAdapter,
        *,
        max_workers: int | None = None,
        provider_name: str = "threadwrap",
    ) -> None:
        self._sync = sync_adapter
        self._provider = provider_name
        self._executor: ThreadPoolExecutor | None = None
        self._max_workers = max_workers
        self._closed = False

    # ------------------------------------------------------- lifecycle

    def _ensure_executor(self) -> ThreadPoolExecutor:
        if self._executor is None:
            self._executor = ThreadPoolExecutor(
                max_workers=self._max_workers,
                thread_name_prefix=f"omp-{self._provider}",
            )
        return self._executor

    async def _run(self, verb: str, fn, *args, **kwargs) -> Any:
        executor = self._ensure_executor()
        # Submit directly so we can attach a done-callback to the
        # underlying ``concurrent.futures.Future`` — that future fires
        # only when the *worker thread* completes, even if the awaiter
        # was cancelled long before. (The asyncio.Future returned by
        # ``loop.run_in_executor`` would fire its callbacks immediately
        # on cancellation and miss the orphan completion entirely.)
        cf_future = executor.submit(partial(fn, *args, **kwargs))

        # Visibility hook for orphaned calls after cancellation (C-CAN-4).
        provider = self._provider

        def _on_done(fut) -> None:
            try:
                exc = fut.exception()
            except Exception:  # pragma: no cover - defensive
                return
            if exc is None:
                _LOG.debug(
                    "orphan call completed after cancellation: provider=%s verb=%s",
                    provider,
                    verb,
                )
            else:
                _LOG.debug(
                    "orphan call failed after cancellation: provider=%s verb=%s err=%s",
                    provider,
                    verb,
                    type(exc).__name__,
                )

        cf_future.add_done_callback(_on_done)
        return await asyncio.wrap_future(cf_future)

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        executor = self._executor
        # Always close the underlying sync adapter even if executor never spun up.
        try:
            sync_close = getattr(self._sync, "close", None)
            if callable(sync_close):
                if executor is None:
                    sync_close()
                else:
                    loop = asyncio.get_running_loop()
                    await loop.run_in_executor(executor, sync_close)
        except Exception:
            pass
        if executor is not None:
            executor.shutdown(wait=False, cancel_futures=True)

    # ------------------------------------------------------------ verbs

    async def add(self, memory: MemoryInput) -> Memory:
        return await self._run("add", self._sync.add, memory)

    async def get(self, id: str) -> Memory:
        return await self._run("get", self._sync.get, id)

    async def update(self, id: str, update: MemoryUpdate) -> Memory:
        return await self._run("update", self._sync.update, id, update)

    async def delete(self, id: str) -> None:
        return await self._run("delete", self._sync.delete, id)

    async def list(  # noqa: A003
        self,
        user_id: str,
        *,
        scope: str | None = None,
        tag: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        limit: int = 50,
        cursor: str | None = None,
    ) -> MemoryPage:
        return await self._run(
            "list",
            self._sync.list,
            user_id,
            scope=scope,
            tag=tag,
            since=since,
            until=until,
            limit=limit,
            cursor=cursor,
        )

    async def search(
        self,
        query: str,
        user_id: str,
        *,
        scope: str | None = None,
        limit: int = 10,
        min_score: float | None = None,
    ) -> list[SearchResult]:
        return await self._run(
            "search",
            self._sync.search,
            query,
            user_id,
            scope=scope,
            limit=limit,
            min_score=min_score,
        )

    async def context(
        self,
        query: str,
        user_id: str,
        *,
        scope: str | None = None,
        token_budget: int = 500,
    ) -> ContextBlock:
        return await self._run(
            "context",
            self._sync.context,
            query,
            user_id,
            scope=scope,
            token_budget=token_budget,
        )

    async def audit(
        self,
        user_id: str,
        *,
        app: str | None = None,
        since: datetime | None = None,
        limit: int = 100,
    ) -> list[AuditEntry]:
        return await self._run(
            "audit",
            self._sync.audit,
            user_id,
            app=app,
            since=since,
            limit=limit,
        )

    async def capabilities(self) -> Capabilities:
        return await self._run("capabilities", self._sync.capabilities)

    async def wait_for_ingest(
        self,
        ids: list[str],
        user_id: str,
        *,
        timeout: float | None = None,
    ) -> None:
        return await self._run(
            "wait_for_ingest",
            self._sync.wait_for_ingest,
            ids,
            user_id,
            timeout=timeout,
        )
