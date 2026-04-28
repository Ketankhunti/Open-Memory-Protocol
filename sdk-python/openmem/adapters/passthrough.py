"""PassthroughAdapter — stub for native OMP HTTP endpoints.

In M1 this only implements the capability probe used by SPEC §11a
auto-detection. Native verb forwarding lands in M2 (see CHANGELOG).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import httpx

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
from .base import BaseAdapter


class PassthroughAdapter(BaseAdapter):
    """Native OMP HTTP adapter (M1: capabilities probe only)."""

    def __init__(
        self,
        base_url: str,
        api_key: str | None = None,
        capabilities: Capabilities | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._capabilities = capabilities

    # --------------------------------------------------------------- probe

    @classmethod
    def _probe(cls, base_url: str, api_key: str | None = None) -> Capabilities | None:
        """Return parsed Capabilities if the endpoint speaks OMP, else None."""
        url = base_url.rstrip("/") + "/capabilities"
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        try:
            resp = httpx.get(url, headers=headers, timeout=5.0)
            resp.raise_for_status()
            payload = resp.json()
        except Exception:
            return None
        if not isinstance(payload, dict) or "omp_version" not in payload:
            return None
        try:
            return Capabilities(**payload)
        except Exception:
            return None

    # --------------------------------------------------- adapter interface

    def capabilities(self) -> Capabilities:
        if self._capabilities is None:
            probed = self._probe(self._base_url, self._api_key)
            if probed is None:
                raise UnsupportedCapabilityError(
                    f"endpoint {self._base_url} did not return OMP capabilities",
                    provider="passthrough",
                )
            self._capabilities = probed
        return self._capabilities

    def _stub(self, verb: str):
        raise NotImplementedError(
            f"PassthroughAdapter.{verb} lands in M2; see CHANGELOG.md"
        )

    def add(self, memory: MemoryInput) -> Memory:  # noqa: D401
        self._stub("add")

    def get(self, id: str) -> Memory:  # noqa: D401
        self._stub("get")

    def update(self, id: str, update: MemoryUpdate) -> Memory:  # noqa: D401
        self._stub("update")

    def delete(self, id: str) -> None:  # noqa: D401
        self._stub("delete")

    def list(  # noqa: A003, D401
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
        self._stub("list")

    def search(  # noqa: D401
        self,
        query: str,
        user_id: str,
        *,
        scope: str | None = None,
        limit: int = 10,
        min_score: float | None = None,
    ) -> list[SearchResult]:
        self._stub("search")

    def context(  # noqa: D401
        self,
        query: str,
        user_id: str,
        *,
        scope: str | None = None,
        token_budget: int = 500,
    ) -> ContextBlock:
        self._stub("context")


__all__ = ["PassthroughAdapter"]
