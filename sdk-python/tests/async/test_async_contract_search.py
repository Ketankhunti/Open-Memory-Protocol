"""Search + context contract tests for `AsyncMemory` (US1).

Per `contracts/async-memory.md` §1 + §5:

* `await mem.search(...)` returns the same shape as sync `Memory.search`
  (`list[SearchResult]`).
* `await mem.context(...)` returns the same shape as sync
  `Memory.context` (`ContextBlock`).
* user_id scoping is enforced — searches scoped to user A MUST NOT
  return memories created for user B.
"""

from __future__ import annotations

import pytest

from openmem.types import ContextBlock, SearchResult


@pytest.fixture(
    params=["postgres", "passthrough", "mem0", "supermemory", "letta"]
)
def provider(request):
    return request.param


async def test_search_returns_list_of_search_results(provider, async_memory_factory):
    mem = await async_memory_factory(provider)
    user_id = "u1-async-search"

    seeded = await mem.add(
        content="user uses pnpm as their default node package manager",
        user_id=user_id,
        scope="coding/tools",
    )
    await mem.wait_for_ingest([seeded.id], user_id)

    results = await mem.search("package manager", user_id, limit=5)
    assert isinstance(results, list)
    for r in results:
        assert isinstance(r, SearchResult)
        assert r.memory.user_id == user_id
        assert isinstance(r.score, (int, float))

    await mem.delete(seeded.id)


async def test_context_returns_context_block(provider, async_memory_factory):
    mem = await async_memory_factory(provider)
    user_id = "u1-async-context"

    seeded = await mem.add(
        content="user prefers dark mode in editor and terminal",
        user_id=user_id,
        scope="ui/preferences",
    )
    await mem.wait_for_ingest([seeded.id], user_id)

    block = await mem.context("dark mode", user_id, token_budget=100)
    assert isinstance(block, ContextBlock)
    assert hasattr(block, "text") and hasattr(block, "citations")

    await mem.delete(seeded.id)


async def test_search_scopes_to_user_id(provider, async_memory_factory):
    """user_id scoping is enforced — A's search MUST NOT return B's memories."""
    mem = await async_memory_factory(provider)
    user_a = "u-async-A"
    user_b = "u-async-B"

    a_record = await mem.add(
        content="alice prefers vim over emacs for editing files", user_id=user_a
    )
    b_record = await mem.add(
        content="bob prefers emacs over vim for editing files", user_id=user_b
    )
    await mem.wait_for_ingest([a_record.id, b_record.id], user_a)
    await mem.wait_for_ingest([a_record.id, b_record.id], user_b)

    a_results = await mem.search("editor preference", user_a, limit=10)
    a_user_ids = {r.memory.user_id for r in a_results}
    assert a_user_ids <= {user_a}, (
        f"user A search leaked memories from other users: {a_user_ids - {user_a}}"
    )

    await mem.delete(a_record.id)
    await mem.delete(b_record.id)
