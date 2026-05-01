"""T013 + C3 — cost table covers add/search/get/delete and ceiling holds."""

from __future__ import annotations

from openmem.eval.cost import estimate, total_estimated


def test_postgres_is_free_for_every_verb() -> None:
    e = estimate("postgres", add_calls=50, search_calls=20, delete_calls=50, get_calls=10)
    assert e.estimated_usd == 0.0


def test_unknown_provider_is_free() -> None:
    e = estimate("nope", add_calls=100, search_calls=100, delete_calls=100)
    assert e.estimated_usd == 0.0


def test_paid_providers_charge_for_each_verb() -> None:
    for prov in ("mem0", "supermemory", "letta"):
        e = estimate(prov, add_calls=10, search_calls=10, delete_calls=10, get_calls=10)
        assert e.estimated_usd > 0


def test_full_run_cost_ceiling_under_50_cents() -> None:
    """SC-007 — 50 facts + 20 queries × 4 providers must stay ≤ $0.50."""
    estimates = [
        estimate(p, add_calls=50, search_calls=20)
        for p in ("postgres", "mem0", "supermemory", "letta")
    ]
    assert total_estimated(estimates) <= 0.50


def test_estimate_records_call_counts() -> None:
    e = estimate("mem0", add_calls=3, search_calls=4, delete_calls=2)
    assert e.add_calls == 3
    assert e.search_calls == 4
    assert e.delete_calls == 2
    assert e.provider == "mem0"
