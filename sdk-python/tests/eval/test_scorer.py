"""T008 + T034 — scorer math: recall@k, MRR, percentiles, suspicious flag."""

from __future__ import annotations

import pytest

from openmem.eval.scorer import (
    compute_metrics,
    mrr,
    recall_at_k,
)
from openmem.eval.types import (
    Metrics,
    ProviderResult,
    QueryResult,
)


def test_recall_at_k_full_hit() -> None:
    assert recall_at_k(["a", "b", "c"], ["a"], k=5) == 1.0


def test_recall_at_k_partial() -> None:
    # 2 of 3 golds appear in top 5
    assert recall_at_k(["a", "x", "b", "y", "z"], ["a", "b", "c"], k=5) == pytest.approx(2 / 3)


def test_recall_at_k_zero_when_no_match() -> None:
    assert recall_at_k(["x", "y", "z"], ["a"], k=5) == 0.0


def test_recall_at_k_truncates_to_k() -> None:
    # gold at position 6 should not count when k=5
    assert recall_at_k(["x", "x", "x", "x", "x", "a"], ["a"], k=5) == 0.0


def test_recall_at_k_empty_results_returns_zero() -> None:
    assert recall_at_k([], ["a"], k=5) == 0.0


def test_mrr_first_rank() -> None:
    assert mrr(["a", "b", "c"], ["a"]) == 1.0


def test_mrr_third_rank() -> None:
    assert mrr(["x", "y", "a"], ["a"]) == pytest.approx(1 / 3)


def test_mrr_no_match_returns_zero() -> None:
    assert mrr(["x", "y"], ["a"]) == 0.0


def test_mrr_empty_returns_zero() -> None:
    assert mrr([], ["a"]) == 0.0


def test_compute_metrics_macro_averages_across_queries() -> None:
    pr = ProviderResult(
        provider="stub",
        query_results=[
            QueryResult(query_id="q1", top_k_fact_ids=["a"]),  # recall=1, rr=1
            QueryResult(query_id="q2", top_k_fact_ids=["x"]),  # recall=0, rr=0
        ],
        ingest_latencies_ms=[10.0, 20.0, 30.0],
        search_latencies_ms=[5.0, 15.0],
    )
    gold = {"q1": ("a",), "q2": ("b",)}
    metrics = compute_metrics(pr, gold)
    assert metrics.recall_at_1 == pytest.approx(0.5)
    assert metrics.recall_at_5 == pytest.approx(0.5)
    assert metrics.mrr == pytest.approx(0.5)
    assert metrics.ingest_p50_ms == pytest.approx(20.0, rel=0.5)
    assert metrics.search_p50_ms > 0


def test_compute_metrics_handles_empty_latencies() -> None:
    pr = ProviderResult(provider="stub")
    metrics = compute_metrics(pr, {})
    assert metrics.ingest_p50_ms == 0.0
    assert metrics.search_p95_ms == 0.0


def test_suspicious_flag_set_when_recall_below_threshold() -> None:
    """T034 — recall@5 < 0.1 sets Metrics.note."""
    pr = ProviderResult(
        provider="bad",
        query_results=[
            QueryResult(query_id="q1", top_k_fact_ids=["x"]),
            QueryResult(query_id="q2", top_k_fact_ids=["y"]),
        ],
    )
    gold = {"q1": ("a",), "q2": ("b",)}
    metrics = compute_metrics(pr, gold)
    assert metrics.recall_at_5 == 0.0
    assert metrics.note is not None
    assert "suspicious" in metrics.note
