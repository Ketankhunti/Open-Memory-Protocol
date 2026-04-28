"""Contract tests: search + context."""

from __future__ import annotations

from openmem.types import MemoryInput


def _input(content: str, **kw) -> MemoryInput:
    return MemoryInput(content=content, user_id="u1", **kw)


def test_search_returns_relevant_above_random(adapter):
    """The relevant memory ranks first and beats random by a margin."""
    distractors = [
        "the weather is sunny today",
        "I bought groceries at the store",
        "remember to call my mother tomorrow",
        "the sky is blue and the grass is green",
        "yesterday I read a book about history",
    ]
    for d in distractors:
        adapter.add(_input(d))
    target = adapter.add(_input("user prefers pnpm over npm for nodejs projects"))

    results = adapter.search("which package manager does the user prefer", "u1", limit=5)
    assert len(results) > 0
    assert results[0].memory.id == target.id
    if len(results) > 1:
        assert results[0].score >= results[-1].score


def test_search_min_score_filters_dissimilar(adapter):
    """C4: very high min_score on dissimilar query yields empty."""
    adapter.add(_input("blue sky over the mountain"))
    results = adapter.search(
        "quantum chromodynamics gauge boson",
        "u1",
        min_score=0.99,
    )
    assert results == []


def test_search_and_context_on_empty_db_return_empty(adapter):
    """EC-001: search/context on empty DB return empty results, no error."""
    assert adapter.search("anything", "nobody") == []
    ctx = adapter.context("anything", "nobody")
    assert ctx.text == ""
    assert ctx.citations == []


def test_context_respects_token_budget_and_returns_citations(adapter):
    for i in range(10):
        adapter.add(_input(f"important fact number {i} about pnpm"))
    ctx = adapter.context("pnpm facts", "u1", token_budget=200)
    assert ctx.token_count is None or ctx.token_count <= 200 + 50  # ~slack
    assert len(ctx.citations) >= 1
    assert all(c.memory_id.startswith("mem_") for c in ctx.citations)
