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

__version__ = "0.5.0"

__all__ = [
    "__version__",
    "Memory",
    "AsyncMemory",
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


# ---------------------------------------------------------------------------
# Lazy `AsyncMemory` import (T019 / FR-026 / contracts §C-EXT-1..3).
#
# `from openmem import AsyncMemory` works iff the `[async]` extra is
# installed (asyncpg + httpx). Without it, the import raises a clear
# `ImportError` whose message contains the exact remediation string.
# Importing `openmem` itself MUST NOT trigger any async dependency
# resolution (C-EXT-3) — that is why this lives in `__getattr__`.
# ---------------------------------------------------------------------------


def __getattr__(name: str):  # noqa: D401 - module-level hook
    if name == "AsyncMemory":
        # FR-026 / C-EXT-1..3: the user must learn *now* that the
        # `[async]` extras are missing — not deep inside a backend
        # call. Eagerly probe `asyncpg` (the only async-only runtime
        # dep; httpx is a base requirement) before exposing the class.
        try:
            import asyncpg  # noqa: F401
        except ImportError as exc:
            raise ImportError(
                "openmem.AsyncMemory requires the async extras. "
                "Install with: pip install 'openmem[async]'"
            ) from exc
        try:
            from .async_memory import AsyncMemory as _AsyncMemory
        except ImportError as exc:  # pragma: no cover - import-time path
            raise ImportError(
                "openmem.AsyncMemory requires the async extras. "
                "Install with: pip install 'openmem[async]'"
            ) from exc
        return _AsyncMemory
    raise AttributeError(f"module 'openmem' has no attribute {name!r}")
