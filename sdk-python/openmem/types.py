"""Pydantic v2 models mirroring `spec/omp-0.1.openapi.yaml` `components/schemas`.

Per Constitution Principle I (Spec-First, NON-NEGOTIABLE), the OpenAPI
document is the source of truth. The mapping is verified at test time by
``test_types_match_openapi.py``.

Per Principles III (forward-compatibility) and V (extensibility via
``x-<provider>`` namespaced fields), every response model inherits from
``_OMPBase`` which sets ``extra="allow"``. This means:

* Unknown future fields on any response are preserved silently
  (Principle III).
* Provider-specific extension keys like ``x-mem0`` round-trip on every
  ``Memory`` (Principle V).
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

# ---------------------------------------------------------------------------
# Shared base for response models
# ---------------------------------------------------------------------------


class _OMPBase(BaseModel):
    """Base class for every OMP response model.

    Sets ``extra="allow"`` so unknown fields (future spec additions or
    ``x-<provider>`` extension keys) round-trip without raising.
    """

    model_config = ConfigDict(extra="allow", populate_by_name=True)


# ---------------------------------------------------------------------------
# MemorySource
# ---------------------------------------------------------------------------


class MemorySource(_OMPBase):
    app: str | None = None
    type: Literal["extracted", "explicit", "imported"] | None = None
    ref: str | None = Field(
        default=None,
        description="Opaque pointer back to source (session id, doc id, etc.).",
    )


# ---------------------------------------------------------------------------
# Memory family
# ---------------------------------------------------------------------------


class MemoryInput(BaseModel):
    """Request body for `POST /memories` (operationId: addMemory)."""

    model_config = ConfigDict(extra="allow")

    content: str
    user_id: str
    scope: str | None = None
    tags: list[str] | None = None
    source: MemorySource | None = None
    confidence: float | None = None
    valid_from: datetime | None = None
    valid_to: datetime | None = None
    supersedes: list[str] | None = None


class MemoryUpdate(BaseModel):
    """Request body for `PATCH /memories/{id}` (operationId: updateMemory)."""

    model_config = ConfigDict(extra="allow")

    content: str | None = None
    scope: str | None = None
    tags: list[str] | None = None
    confidence: float | None = None
    valid_to: datetime | None = None
    supersedes: list[str] | None = None


class Memory(_OMPBase):
    """An OMP memory record. Required: id, content, user_id, created_at."""

    id: str
    content: str
    user_id: str
    created_at: datetime
    scope: str | None = None
    tags: list[str] | None = None
    source: MemorySource | None = None
    confidence: float | None = None
    valid_from: datetime | None = None
    valid_to: datetime | None = None
    supersedes: list[str] | None = None
    embedding_model: str | None = None
    updated_at: datetime | None = None
    status: Literal["queued", "indexing", "done", "failed"] | None = None


class MemoryPage(_OMPBase):
    """Paginated list of memories. Cursor is opaque to clients."""

    items: list[Memory]
    next_cursor: str | None = None


# ---------------------------------------------------------------------------
# Search & context
# ---------------------------------------------------------------------------


class SearchResult(_OMPBase):
    """A single result from `searchMemories`.

    score: cosine similarity in 0..1; higher = more similar.
    """

    memory: Memory
    score: float


class _Citation(_OMPBase):
    memory_id: str
    score: float


class ContextBlock(_OMPBase):
    """Pre-ranked, prompt-ready block returned by `getContext`."""

    text: str
    citations: list[_Citation]
    token_count: int | None = None


# ---------------------------------------------------------------------------
# Capabilities
# ---------------------------------------------------------------------------


class CapabilityFeatures(_OMPBase):
    vector_search: bool | None = None
    keyword_search: bool | None = None
    graph_queries: bool | None = None
    temporal: bool | None = None
    scopes: Literal["native", "tags", "none"] | None = None
    max_content_length: int | None = None
    supports_e2e: bool | None = None
    supports_audit: bool | None = None
    supports_supersession: bool | None = None


class CapabilityLimits(_OMPBase):
    rate_limit_per_minute: int | None = None
    max_search_results: int | None = None


VerbName = Literal[
    "add", "search", "get", "update", "delete", "list", "context", "audit"
]


class Capabilities(_OMPBase):
    omp_version: str
    provider: str
    verbs: list[VerbName]
    features: CapabilityFeatures
    limits: CapabilityLimits | None = None


# ---------------------------------------------------------------------------
# Audit
# ---------------------------------------------------------------------------


AuditAction = Literal[
    "add", "search", "get", "update", "delete", "list", "context"
]


class AuditEntry(_OMPBase):
    timestamp: datetime | None = None
    app: str | None = None
    action: AuditAction | None = None
    memory_id: str | None = None
    scope: str | None = None
    request_id: str | None = None


# ---------------------------------------------------------------------------
# Error envelope (mirrors `components/schemas/Error`)
# ---------------------------------------------------------------------------


class _ErrorBody(_OMPBase):
    code: str
    message: str
    type: str
    provider: str | None = None
    request_id: str | None = None


class Error(_OMPBase):
    """Wire-format error envelope returned by every OMP endpoint on failure."""

    error: _ErrorBody


__all__ = [
    "MemorySource",
    "MemoryInput",
    "MemoryUpdate",
    "Memory",
    "MemoryPage",
    "SearchResult",
    "ContextBlock",
    "CapabilityFeatures",
    "CapabilityLimits",
    "Capabilities",
    "AuditEntry",
    "Error",
]
