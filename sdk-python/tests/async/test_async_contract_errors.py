"""Error parity contract tests for `AsyncMemory` (US1, contracts §5).

Per `contracts/async-memory.md` §5 (C-ERR-1, C-ERR-2, C-ERR-3) every
condition where sync `Memory.<verb>` raises an exception class MUST also
raise that **same class** on `AsyncMemory.<verb>` for the same input.

Specifically:
* C-ERR-3: empty / whitespace `user_id` raises `InvalidRequestError`
  *before* any backend call (defends cross-user leakage).
* C-ERR-1: `await mem.get(unknown_id)` raises `NotFoundError`
  (or `ProviderError(code="ingestion_timeout")` on async-ingest providers).
"""

from __future__ import annotations

import pytest

from openmem.errors import InvalidRequestError, NotFoundError, ProviderError


@pytest.fixture(
    params=["postgres", "passthrough", "mem0", "supermemory", "letta"]
)
def provider(request):
    return request.param


async def test_empty_user_id_raises_invalid_request(provider, async_memory_factory):
    """C-ERR-3: empty `user_id` raises BEFORE backend touch."""
    mem = await async_memory_factory(provider)
    with pytest.raises(InvalidRequestError):
        await mem.add(content="will not be persisted", user_id="")


async def test_whitespace_user_id_raises_invalid_request(provider, async_memory_factory):
    mem = await async_memory_factory(provider)
    with pytest.raises(InvalidRequestError):
        await mem.add(content="will not be persisted", user_id="   ")


async def test_get_unknown_id_raises_not_found(provider, async_memory_factory):
    """C-ERR-1: unknown id raises NotFoundError (or ingestion_timeout)."""
    if provider == "letta":
        pytest.skip("letta does not advertise verb 'get'")
    mem = await async_memory_factory(provider)
    with pytest.raises((NotFoundError, ProviderError)) as excinfo:
        await mem.get("mem_does_not_exist_async_xxxx")
    if isinstance(excinfo.value, ProviderError):
        assert excinfo.value.code == "ingestion_timeout"
