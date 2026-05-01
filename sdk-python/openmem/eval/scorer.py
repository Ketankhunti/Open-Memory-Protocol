"""Metric calculations for the eval kit.

Implements per-query recall@k and reciprocal rank, then macro-averages
across queries. Latency percentiles use ``statistics.quantiles(n=100)``
so we get p50/p95/p99 with no third-party dependency.
"""

from __future__ import annotations

import statistics
from typing import Iterable, Mapping, Sequence

from openmem.eval.types import Metrics, ProviderResult

_SUSPICIOUS_RECALL_AT_5 = 0.10


def recall_at_k(
    top_k_fact_ids: Sequence[str],
    gold_fact_ids: Sequence[str],
    *,
    k: int,
) -> float:
    if not gold_fact_ids:
        return 0.0
    head = list(top_k_fact_ids)[:k]
    hits = sum(1 for g in gold_fact_ids if g in head)
    return hits / len(gold_fact_ids)


def mrr(top_k_fact_ids: Sequence[str], gold_fact_ids: Sequence[str]) -> float:
    gold = set(gold_fact_ids)
    for rank, fid in enumerate(top_k_fact_ids, start=1):
        if fid in gold:
            return 1.0 / rank
    return 0.0


def _percentile(values: Sequence[float], p: float) -> float:
    """Return the `p`-th percentile (0-100). Empty → 0.0; single value → itself."""
    if not values:
        return 0.0
    if len(values) == 1:
        return float(values[0])
    quantiles = statistics.quantiles(values, n=100, method="inclusive")
    # quantiles returns 99 cut points: index i corresponds to (i+1)th percentile
    idx = max(0, min(len(quantiles) - 1, int(round(p)) - 1))
    return float(quantiles[idx])


def compute_metrics(
    pr: ProviderResult,
    gold: Mapping[str, Sequence[str]],
) -> Metrics:
    """Compute aggregate Metrics for a ProviderResult."""
    recall_1: list[float] = []
    recall_5: list[float] = []
    rrs: list[float] = []
    for qr in pr.query_results:
        gold_ids = gold.get(qr.query_id, ())
        recall_1.append(recall_at_k(qr.top_k_fact_ids, gold_ids, k=1))
        recall_5.append(recall_at_k(qr.top_k_fact_ids, gold_ids, k=5))
        rrs.append(mrr(qr.top_k_fact_ids, gold_ids))

    r5 = _mean(recall_5)
    note: str | None = None
    if pr.query_results and r5 < _SUSPICIOUS_RECALL_AT_5:
        note = (
            f"suspicious recall@5={r5:.3f} below {_SUSPICIOUS_RECALL_AT_5:.2f}; "
            "check ingest configuration"
        )

    metrics = Metrics(
        recall_at_1=_mean(recall_1),
        recall_at_5=r5,
        mrr=_mean(rrs),
        ingest_p50_ms=_percentile(pr.ingest_latencies_ms, 50),
        ingest_p95_ms=_percentile(pr.ingest_latencies_ms, 95),
        ingest_p99_ms=_percentile(pr.ingest_latencies_ms, 99),
        search_p50_ms=_percentile(pr.search_latencies_ms, 50),
        search_p95_ms=_percentile(pr.search_latencies_ms, 95),
        search_p99_ms=_percentile(pr.search_latencies_ms, 99),
        error_count=pr.metrics.error_count,
        note=note,
    )
    return metrics


def _mean(values: Iterable[float]) -> float:
    vals = list(values)
    if not vals:
        return 0.0
    return sum(vals) / len(vals)


__all__ = ["recall_at_k", "mrr", "compute_metrics"]
