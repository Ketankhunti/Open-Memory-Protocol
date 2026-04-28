"""Open Memory Protocol (OMP) Python SDK.

One API for any AI memory provider.

Quickstart::

    from openmem import Memory
    mem = Memory(provider="postgres", url="postgres://localhost/omp")
    mem.add(content="user prefers pnpm over npm", user_id="u1")
    print(mem.context("package manager", user_id="u1").text)
"""

from __future__ import annotations

from .errors import (
    InvalidRequestError,
    NotFoundError,
    OMPError,
    ProviderError,
    RateLimitedError,
    ScopeDeniedError,
    UnauthorizedError,
    UnsupportedCapabilityError,
    UnsupportedProviderError,
)
from .memory import Memory
from .types import (
    AuditEntry,
    Capabilities,
    CapabilityFeatures,
    CapabilityLimits,
    ContextBlock,
)
from .types import Memory as MemoryRecord
from .types import (
    MemoryInput,
    MemoryPage,
    MemorySource,
    MemoryUpdate,
    SearchResult,
)

__version__ = "0.1.0"

__all__ = [
    "__version__",
    "Memory",
    "MemoryRecord",
    "MemoryInput",
    "MemoryUpdate",
    "MemoryPage",
    "MemorySource",
    "SearchResult",
    "ContextBlock",
    "Capabilities",
    "CapabilityFeatures",
    "CapabilityLimits",
    "AuditEntry",
    "OMPError",
    "UnauthorizedError",
    "ScopeDeniedError",
    "NotFoundError",
    "InvalidRequestError",
    "RateLimitedError",
    "UnsupportedCapabilityError",
    "ProviderError",
    "UnsupportedProviderError",
]
