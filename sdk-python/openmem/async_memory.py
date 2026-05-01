"""User-facing :class:`AsyncMemory` facade (T018 / M3.2).

Async mirror of :class:`openmem.memory.Memory`. The constructor
performs **zero blocking I/O** (data-model AM-INV-3 / C-LIFE-1) — pool
and HTTP client are built lazily on first verb call or on
``__aenter__``.

Provider routing:

* ``postgres`` → :class:`AsyncPostgresAdapter` (asyncpg, native cancel)
* ``passthrough`` → :class:`AsyncPassthroughAdapter` (httpx async, native cancel)
* ``mem0`` / ``supermemory`` / ``letta`` → :class:`AsyncThreadwrapAdapter`
  wrapping the corresponding sync adapter (best-effort cancel)

Cross-loop safety (C-LOOP-1): the loop id is captured on the first verb
call. A subsequent verb on a different loop raises
``RuntimeError("AsyncMemory is bound to a different event loop")``
*before* any backend call.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any

from .adapters.async_base import AsyncBaseAdapter
from .errors import UnsupportedProviderError
from .types import (
    AuditEntry,
    Capabilities,
    ContextBlock,
    Memory as _MemoryRecord,
    MemoryInput,
    MemoryPage,
    MemorySource,
    MemoryUpdate,
    SearchResult,
)

__all__ = ["AsyncMemory"]


_NATIVE_PROVIDERS = ("postgres", "passthrough")
_THREADWRAP_PROVIDERS = ("mem0", "supermemory", "letta")


def _resolve_async_adapter(
    provider: str,
    *,
    executor_max_workers: int | None,
    **config: Any,
) -> AsyncBaseAdapter:
    """Return the concrete async adapter for ``provider``."""
    if provider == "postgres":
        from .adapters.async_postgres import AsyncPostgresAdapter
        from .adapters.embedder import FakeEmbedder, OpenAIEmbedder

        embedder = config.pop("embedder", None)
        if embedder is None:
            embedder = (
                OpenAIEmbedder()
                if config.pop("use_openai", False)
                else FakeEmbedder()
            )
        url = config.pop("url", None) or config.pop("dsn", None)
        if not url:
            raise ValueError("postgres provider requires url=...")
        return AsyncPostgresAdapter(url=url, embedder=embedder, **config)

    if provider == "passthrough":
        from .adapters.async_passthrough import AsyncPassthroughAdapter

        base_url = config.pop("base_url", None)
        if not base_url:
            raise ValueError("passthrough provider requires base_url=...")
        return AsyncPassthroughAdapter(
            base_url=base_url,
            api_key=config.pop("api_key", None),
            transport=config.pop("transport", None),
            timeout=config.pop("timeout", 30.0),
            capabilities=config.pop("capabilities", None),
        )

    if provider in _THREADWRAP_PROVIDERS:
        from .adapters.async_threadwrap import AsyncThreadwrapAdapter

        # Build the sync adapter DIRECTLY (do NOT go through
        # `openmem.memory._resolve_adapter`). The shared resolver pops
        # `base_url` and runs a passthrough auto-probe before falling
        # through, which corrupts test transports that target a non-OMP
        # endpoint URL. Sync tests sidestep `_resolve_adapter` for the
        # same reason — see `tests/conftest.py::supermemory_adapter`.
        sync_adapter = _build_sync_adapter_direct(provider, **config)
        return AsyncThreadwrapAdapter(
            sync_adapter,
            max_workers=executor_max_workers,
            provider_name=provider,
        )

    raise UnsupportedProviderError(
        f"unknown async provider {provider!r}"
    )


def _build_sync_adapter_direct(provider: str, **config: Any):
    """Construct a translation sync adapter without auto-probe side effects."""
    api_key = config.pop("api_key", None)
    if not api_key:
        raise ValueError(f"{provider} provider requires api_key=...")
    if provider == "mem0":
        from .adapters.mem0 import Mem0Adapter

        return Mem0Adapter(
            api_key=api_key,
            host=config.pop("host", "https://api.mem0.ai"),
            client=config.pop("client", None),
        )
    if provider == "supermemory":
        from .adapters.supermemory import DEFAULT_BASE_URL, SupermemoryAdapter

        return SupermemoryAdapter(
            api_key=api_key,
            base_url=config.pop("base_url", DEFAULT_BASE_URL),
            transport=config.pop("transport", None),
        )
    if provider == "letta":
        from .adapters.letta import LettaAdapter

        return LettaAdapter(
            api_key=api_key,
            base_url=config.pop("base_url", None),
            client=config.pop("client", None),
        )
    raise UnsupportedProviderError(
        f"unknown threadwrap provider {provider!r}"
    )


class AsyncMemory:
    """Async OMP client. Mirrors :class:`openmem.Memory` 1:1.

    Example::

        from openmem import AsyncMemory
        async with AsyncMemory(provider="postgres", url="postgres://...") as mem:
            m = await mem.add(content="user prefers pnpm", user_id="u1")
            for r in await mem.search("package manager", user_id="u1"):
                print(r.memory.content, r.score)
    """

    def __init__(
        self,
        provider: str = "postgres",
        **config: Any,
    ) -> None:
        # `executor_max_workers` is the only async-only kwarg; pull it
        # out and forward to the threadwrap adapter. Native adapters
        # silently ignore it (AM-INV-7).
        executor_max_workers = config.pop("executor_max_workers", None)
        self._adapter: AsyncBaseAdapter = _resolve_async_adapter(
            provider,
            executor_max_workers=executor_max_workers,
            **config,
        )
        self._capabilities: Capabilities | None = None
        self._closed: bool = False
        self._loop_id: int | None = None

    # --------------------------------------------------------- guards

    def _check_open(self) -> None:
        if self._closed:
            raise RuntimeError("AsyncMemory is closed")

    def _check_loop(self) -> None:
        try:
            current = id(asyncio.get_running_loop())
        except RuntimeError:
            # No running loop — we can't bind yet; the verb call itself
            # will fail naturally if it needs one.
            return
        if self._loop_id is None:
            self._loop_id = current
            return
        if self._loop_id != current:
            raise RuntimeError(
                "AsyncMemory is bound to a different event loop"
            )

    # --------------------------------------------------------- lifecycle

    async def __aenter__(self) -> "AsyncMemory":
        # Eagerly bind the loop so subsequent verbs detect cross-loop misuse.
        self._check_loop()
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.close()

    async def close(self) -> None:
        """Close the adapter. Idempotent (C-LIFE-5)."""
        if self._closed:
            return
        self._closed = True
        try:
            await self._adapter.close()
        except Exception:
            pass

    # ------------------------------------------------- verb passthrough

    async def add(
        self,
        *,
        content: str,
        user_id: str,
        scope: str | None = None,
        tags: list[str] | None = None,
        source: MemorySource | dict[str, Any] | None = None,
        confidence: float | None = None,
        valid_from: datetime | None = None,
        valid_to: datetime | None = None,
        supersedes: list[str] | None = None,
        **extensions: Any,
    ) -> _MemoryRecord:
        self._check_open()
        self._check_loop()
        # C-ERR-3: validate user_id BEFORE building MemoryInput so
        # threadwrap providers (mem0, supermemory, letta) cannot reach a
        # backend with empty user_id and leak across users.
        from .adapters._validation import require_user_id

        require_user_id(user_id, provider="async")
        if isinstance(source, dict):
            source = MemorySource(**source)
        payload = MemoryInput(
            content=content,
            user_id=user_id,
            scope=scope,
            tags=tags,
            source=source,
            confidence=confidence,
            valid_from=valid_from,
            valid_to=valid_to,
            supersedes=supersedes,
            **extensions,
        )
        return await self._adapter.add(payload)

    async def get(self, id: str) -> _MemoryRecord:
        self._check_open()
        self._check_loop()
        return await self._adapter.get(id)

    async def update(
        self,
        id: str,
        *,
        content: str | None = None,
        scope: str | None = None,
        tags: list[str] | None = None,
        confidence: float | None = None,
        valid_to: datetime | None = None,
        supersedes: list[str] | None = None,
    ) -> _MemoryRecord:
        self._check_open()
        self._check_loop()
        return await self._adapter.update(
            id,
            MemoryUpdate(
                content=content,
                scope=scope,
                tags=tags,
                confidence=confidence,
                valid_to=valid_to,
                supersedes=supersedes,
            ),
        )

    async def delete(self, id: str) -> None:
        self._check_open()
        self._check_loop()
        await self._adapter.delete(id)

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
        self._check_open()
        self._check_loop()
        return await self._adapter.list(
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
        self._check_open()
        self._check_loop()
        return await self._adapter.search(
            query, user_id, scope=scope, limit=limit, min_score=min_score
        )

    async def context(
        self,
        query: str,
        user_id: str,
        *,
        scope: str | None = None,
        token_budget: int = 500,
    ) -> ContextBlock:
        self._check_open()
        self._check_loop()
        return await self._adapter.context(
            query, user_id, scope=scope, token_budget=token_budget
        )

    async def audit(
        self,
        user_id: str,
        *,
        app: str | None = None,
        since: datetime | None = None,
        limit: int = 100,
    ) -> list[AuditEntry]:
        self._check_open()
        self._check_loop()
        return await self._adapter.audit(
            user_id, app=app, since=since, limit=limit
        )

    async def capabilities(self) -> Capabilities:
        self._check_open()
        self._check_loop()
        if self._capabilities is None:
            self._capabilities = await self._adapter.capabilities()
        return self._capabilities

    async def wait_for_ingest(
        self,
        ids: list[str],
        user_id: str,
        *,
        timeout: float | None = None,
    ) -> None:
        self._check_open()
        self._check_loop()
        await self._adapter.wait_for_ingest(ids, user_id, timeout=timeout)
