"""Abstract base class for OMP adapters.

Every translation or passthrough adapter must subclass `BaseAdapter` and
implement the required verbs (`add`, `search`, `get`, `update`, `delete`,
`list`, `context`, `capabilities`). `audit` is optional — the default
raises `UnsupportedCapabilityError`.

Per Constitution Principle II (NON-NEGOTIABLE), every concrete adapter
must pass the parametrized contract suite at
`sdk-python/tests/test_contract_*.py`.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime

from ..errors import UnsupportedCapabilityError
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


class BaseAdapter(ABC):
    """Abstract base class for an OMP adapter."""

    @abstractmethod
    def add(self, memory: MemoryInput) -> Memory:
        """Create a memory."""

    @abstractmethod
    def get(self, id: str) -> Memory:
        """Fetch a memory by id. Raise `NotFoundError` on miss."""

    @abstractmethod
    def update(self, id: str, update: MemoryUpdate) -> Memory:
        """Update / supersede a memory."""

    @abstractmethod
    def delete(self, id: str) -> None:
        """Forget a memory."""

    @abstractmethod
    def list(
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
        """List memories with optional filters and keyset pagination."""

    @abstractmethod
    def search(
        self,
        query: str,
        user_id: str,
        *,
        scope: str | None = None,
        limit: int = 10,
        min_score: float | None = None,
    ) -> list[SearchResult]:
        """Semantic + keyword search."""

    @abstractmethod
    def context(
        self,
        query: str,
        user_id: str,
        *,
        scope: str | None = None,
        token_budget: int = 500,
    ) -> ContextBlock:
        """Return a prompt-ready, ranked context block."""

    @abstractmethod
    def capabilities(self) -> Capabilities:
        """Return this provider's capability matrix."""

    def audit(
        self,
        user_id: str,
        *,
        app: str | None = None,
        since: datetime | None = None,
        limit: int = 100,
    ) -> list[AuditEntry]:
        """Audit log of memory access. Optional verb.

        Default raises ``UnsupportedCapabilityError``; override in adapters
        whose providers expose audit data.
        """
        raise UnsupportedCapabilityError(
            "audit is not supported by this provider",
            provider=self.capabilities().provider,
        )


__all__ = ["BaseAdapter"]
