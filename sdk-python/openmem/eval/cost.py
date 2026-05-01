"""Cost estimation for live runs.

Per FR-006 and SC-007: a full live run across all four providers must
remain ≤ $0.50 with the bundled 50/20 dataset. Numbers below are
deliberately *upper-bound* estimates expressed in USD per call so the
dry-run preview is conservative.

postgres is treated as zero-cost (self-hosted).
"""

from __future__ import annotations

from dataclasses import dataclass

# USD per verb call. Conservative upper bounds informed by published
# pricing pages as of 2025-01. These are not invoiced; they only feed
# the dry-run cost ceiling.
_COSTS: dict[str, dict[str, float]] = {
    "postgres":    {"add": 0.0,    "search": 0.0,    "get": 0.0,    "delete": 0.0},
    "mem0":        {"add": 0.0010, "search": 0.0010, "get": 0.0001, "delete": 0.0001},
    "supermemory": {"add": 0.0008, "search": 0.0008, "get": 0.0001, "delete": 0.0001},
    "letta":       {"add": 0.0015, "search": 0.0015, "get": 0.0001, "delete": 0.0001},
}


@dataclass(frozen=True)
class CostEstimate:
    provider: str
    add_calls: int
    search_calls: int
    delete_calls: int
    estimated_usd: float


def estimate(
    provider: str,
    *,
    add_calls: int,
    search_calls: int,
    delete_calls: int = 0,
    get_calls: int = 0,
) -> CostEstimate:
    """Return a CostEstimate for `provider`. Unknown providers cost $0."""
    table = _COSTS.get(provider, {"add": 0.0, "search": 0.0, "get": 0.0, "delete": 0.0})
    total = (
        table["add"] * add_calls
        + table["search"] * search_calls
        + table["get"] * get_calls
        + table["delete"] * delete_calls
    )
    return CostEstimate(
        provider=provider,
        add_calls=add_calls,
        search_calls=search_calls,
        delete_calls=delete_calls,
        estimated_usd=round(total, 4),
    )


def total_estimated(estimates: list[CostEstimate]) -> float:
    return round(sum(e.estimated_usd for e in estimates), 4)


__all__ = ["CostEstimate", "estimate", "total_estimated"]
