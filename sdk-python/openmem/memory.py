"""User-facing `Memory` facade.

Wraps an underlying adapter and exposes the OMP verbs as plain Python
methods so applications never import an adapter class directly. This is
what guarantees substitutability (Constitution Principle IV): swap the
``provider=`` string and the *exact same calls* keep working.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from .adapters.base import BaseAdapter
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


def _resolve_adapter(provider: str, **config: Any) -> BaseAdapter:
    """Resolve a provider string to a concrete adapter instance.

    Implements SPEC §11a auto-detection:

    1. If ``base_url`` is provided, probe ``GET {base_url}/capabilities``.
       If the endpoint returns a payload with ``omp_version``, use
       ``PassthroughAdapter`` regardless of the ``provider`` string.
    2. Otherwise, dispatch to the translation adapter registered for
       ``provider`` in ``TRANSLATION_ADAPTERS``.
    3. If neither path works, raise ``UnsupportedProviderError``.
    """
    base_url = config.pop("base_url", None)
    api_key = config.get("api_key")
    if base_url:
        from .adapters.passthrough import PassthroughAdapter

        caps = PassthroughAdapter._probe(base_url, api_key)
        if caps is not None:
            return PassthroughAdapter(
                base_url=base_url, api_key=api_key, capabilities=caps
            )
        # Fall through to translation if a known provider is also given

    if provider == "postgres":
        from .adapters.embedder import FakeEmbedder, OpenAIEmbedder
        from .adapters.postgres import PostgresAdapter

        embedder = config.pop("embedder", None)
        if embedder is None:
            embedder = (
                OpenAIEmbedder() if config.pop("use_openai", False) else FakeEmbedder()
            )
        url = config.pop("url", None) or config.pop("dsn", None)
        if not url:
            raise ValueError("postgres provider requires url=...")
        return PostgresAdapter(url=url, embedder=embedder)

    if provider == "mem0":
        from .adapters.mem0 import Mem0Adapter

        if not api_key:
            raise ValueError("mem0 provider requires api_key=...")
        return Mem0Adapter(
            api_key=api_key,
            host=config.pop("host", "https://api.mem0.ai"),
            client=config.pop("client", None),
        )

    if provider == "supermemory":
        from .adapters.supermemory import SupermemoryAdapter

        if not api_key:
            raise ValueError("supermemory provider requires api_key=...")
        return SupermemoryAdapter(
            api_key=api_key,
            base_url=config.pop("base_url", "https://api.supermemory.ai/v1"),
            transport=config.pop("transport", None),
        )

    if provider == "letta":
        from .adapters.letta import LettaAdapter

        if not api_key:
            raise ValueError("letta provider requires api_key=...")
        return LettaAdapter(
            api_key=api_key,
            base_url=config.pop("base_url", None),
            client=config.pop("client", None),
        )

    raise UnsupportedProviderError(
        f"unknown provider {provider!r}; pass base_url=... for native OMP"
    )


# Translation adapters known to the SDK.
TRANSLATION_ADAPTERS = ("postgres", "mem0", "supermemory", "letta")


class Memory:
    """User-facing OMP client.

    Example::

        from openmem import Memory
        mem = Memory(provider="postgres", url="postgres://...")
        m = mem.add(content="user prefers pnpm", user_id="u1")
        for r in mem.search("package manager", user_id="u1"):
            print(r.memory.content, r.score)
    """

    def __init__(self, provider: str = "postgres", **config: Any) -> None:
        self._adapter: BaseAdapter = _resolve_adapter(provider, **config)
        self._capabilities: Capabilities | None = None

    # ----------------------------------------------------- adapter passthrough

    def add(
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
        return self._adapter.add(payload)

    def get(self, id: str) -> _MemoryRecord:
        return self._adapter.get(id)

    def update(
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
        return self._adapter.update(
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

    def delete(self, id: str) -> None:
        self._adapter.delete(id)

    def list(  # noqa: A003
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
        return self._adapter.list(
            user_id,
            scope=scope,
            tag=tag,
            since=since,
            until=until,
            limit=limit,
            cursor=cursor,
        )

    def search(
        self,
        query: str,
        user_id: str,
        *,
        scope: str | None = None,
        limit: int = 10,
        min_score: float | None = None,
    ) -> list[SearchResult]:
        return self._adapter.search(
            query, user_id, scope=scope, limit=limit, min_score=min_score
        )

    def context(
        self,
        query: str,
        user_id: str,
        *,
        scope: str | None = None,
        token_budget: int = 500,
    ) -> ContextBlock:
        return self._adapter.context(
            query, user_id, scope=scope, token_budget=token_budget
        )

    def audit(
        self,
        user_id: str,
        *,
        app: str | None = None,
        since: datetime | None = None,
        limit: int = 100,
    ) -> list[AuditEntry]:
        return self._adapter.audit(user_id, app=app, since=since, limit=limit)

    def capabilities(self) -> Capabilities:
        if self._capabilities is None:
            self._capabilities = self._adapter.capabilities()
        return self._capabilities


__all__ = ["Memory", "_resolve_adapter"]
