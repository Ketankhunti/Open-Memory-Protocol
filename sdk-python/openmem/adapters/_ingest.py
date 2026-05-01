"""Bounded-poll helper for asynchronous-ingestion providers (mem0, supermemory).

Per `specs/003-m2-1-live/data-model.md` §2 and FR-105 / FR-110 / EC-101:
adapters whose `add()` is async return a `Memory(status="queued", ...)`
immediately. `get(id)` then polls the provider until the record reaches
`status="done"` OR a bounded budget elapses, at which point we raise
`ProviderError(code="ingestion_timeout", ...)`.

The poll uses exponential back-off:
    delay_n = min(max_delay, base_delay * 2**n)

Security:
- `timeout` MUST be a positive float; non-positive raises `ValueError`
  to prevent silent infinite loops on misconfiguration.
- Callers SHOULD source `timeout` from `OMP_INGEST_TIMEOUT` via
  `read_ingest_timeout_env()`, which clamps the value to (0, 600] to
  prevent runaway-poll DoS against test infrastructure (data-model.md §4a).
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any, Callable, TypeVar

from openmem.errors import ProviderError

T = TypeVar("T")
_LOG = logging.getLogger(__name__)

DEFAULT_INGEST_TIMEOUT = 60
MAX_INGEST_TIMEOUT = 600


def read_ingest_timeout_env(env: dict[str, str] | None = None) -> int:
    """Parse OMP_INGEST_TIMEOUT with strict, security-aware bounds.

    Returns DEFAULT_INGEST_TIMEOUT on any of: missing, empty, non-numeric,
    non-integer, <= 0, or > MAX_INGEST_TIMEOUT. Emits a warning on
    out-of-range values.
    """
    src = env if env is not None else os.environ
    raw = (src.get("OMP_INGEST_TIMEOUT") or "").strip()
    if not raw:
        return DEFAULT_INGEST_TIMEOUT
    try:
        value = int(raw)
    except ValueError:
        _LOG.warning(
            "OMP_INGEST_TIMEOUT=%r is not an integer; using default %d",
            raw,
            DEFAULT_INGEST_TIMEOUT,
        )
        return DEFAULT_INGEST_TIMEOUT
    if value <= 0 or value > MAX_INGEST_TIMEOUT:
        _LOG.warning(
            "OMP_INGEST_TIMEOUT=%d out of range (0, %d]; using default %d",
            value,
            MAX_INGEST_TIMEOUT,
            DEFAULT_INGEST_TIMEOUT,
        )
        return DEFAULT_INGEST_TIMEOUT
    return value


def poll_until(
    fn: Callable[[], T | None],
    timeout: float,
    *,
    base_delay: float = 1.0,
    max_delay: float = 5.0,
    provider: str,
    on_timeout_details: dict[str, Any] | None = None,
    sleeper: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> T:
    """Call ``fn()`` repeatedly until it returns a truthy value or budget elapses.

    Parameters
    ----------
    fn:
        Zero-arg callable. Return any truthy value to stop polling; return
        ``None`` (or a falsy sentinel) to continue. Exceptions from ``fn``
        are propagated immediately (not retried).
    timeout:
        Total budget in seconds. MUST be > 0.
    base_delay, max_delay:
        Exponential back-off bounds: ``delay_n = min(max_delay, base_delay * 2**n)``.
    provider:
        Provider name for the raised ``ProviderError`` (e.g. ``"mem0"``).
    on_timeout_details:
        Extra context (e.g. ``{"event_id": ...}``) merged into the raised
        ``ProviderError.details`` shape (we attach it as the ``message``
        suffix and on the exception's ``details`` attribute when present).
    sleeper, clock:
        Test seams.
    """
    if timeout <= 0:
        raise ValueError(
            f"timeout must be positive, got {timeout!r} (M2.1 _ingest.poll_until)"
        )
    deadline = clock() + timeout
    attempt = 0
    while True:
        # Per docstring: exceptions from `fn` propagate immediately. Callers
        # that wish to keep polling on a specific exception MUST catch it
        # inside `fn` and return `None`. This guarantees that real errors
        # (auth failure, rate-limit, malformed response) cannot be hidden
        # by the poll loop and silently turned into ingestion-timeouts.
        result = fn()
        if result:
            return result  # type: ignore[return-value]
        now = clock()
        remaining = deadline - now
        if remaining <= 0:
            elapsed = timeout - max(remaining, 0)
            details = dict(on_timeout_details or {})
            details["elapsed"] = round(elapsed, 3)
            err = ProviderError(
                f"ingestion timeout after {elapsed:.1f}s",
                provider=provider,
                code="ingestion_timeout",
            )
            err.details = details  # type: ignore[attr-defined]
            raise err
        delay = min(max_delay, base_delay * (2 ** attempt))
        delay = min(delay, remaining)
        sleeper(delay)
        attempt += 1
