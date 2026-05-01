"""Internal data structures for the eval kit.

Mirrors `specs/004-eval-kit/data-model.md`. None of these types are part of
the OMP protocol surface; they are harness-internal.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


# ---------------------------------------------------------------------------
# Dataset records
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Fact:
    fact_id: str
    content: str
    tags: tuple[str, ...] = ()


@dataclass(frozen=True)
class Query:
    query_id: str
    query: str
    gold_fact_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class Dataset:
    facts: tuple[Fact, ...]
    queries: tuple[Query, ...]
    dataset_hash: str  # 12-hex-char SHA-256 prefix


# ---------------------------------------------------------------------------
# Run configuration
# ---------------------------------------------------------------------------


@dataclass
class RunConfig:
    providers: tuple[str, ...] = ("postgres",)
    live: bool = False
    report_path: Path = Path("eval-report.md")
    trace_path: Path = Path("eval-trace.jsonl")
    sample: Optional[int] = None
    cost_threshold_usd: float = 1.00
    yes: bool = False
    cleanup: bool = False
    verbose: bool = False
    top_k: int = 5
    run_id: str = ""  # set in __post_init__

    def __post_init__(self) -> None:
        if not self.run_id:
            import uuid

            object.__setattr__(self, "run_id", uuid.uuid4().hex[:12])

    @property
    def dry_run(self) -> bool:
        return not self.live

    def user_id(self) -> str:
        return f"eval-{self.run_id}"


# ---------------------------------------------------------------------------
# Per-query / per-error / per-provider records
# ---------------------------------------------------------------------------


@dataclass
class QueryResult:
    query_id: str
    top_k_fact_ids: list[str] = field(default_factory=list)
    latency_ms: float = 0.0
    error: Optional[str] = None


@dataclass
class ErrorRecord:
    verb: str
    error_class: str
    message: str
    ts: str
    query_id: Optional[str] = None
    fact_id: Optional[str] = None


@dataclass
class Metrics:
    recall_at_1: float = 0.0
    recall_at_5: float = 0.0
    recall_at_10: float = 0.0
    mrr: float = 0.0
    ingest_p50_ms: float = 0.0
    ingest_p95_ms: float = 0.0
    ingest_p99_ms: float = 0.0
    search_p50_ms: float = 0.0
    search_p95_ms: float = 0.0
    search_p99_ms: float = 0.0
    error_count: int = 0
    note: Optional[str] = None


@dataclass
class ProviderResult:
    provider: str
    status: str = "ok"  # ok | skipped | partial | failed
    skip_reason: Optional[str] = None
    ingest_latencies_ms: list[float] = field(default_factory=list)
    search_latencies_ms: list[float] = field(default_factory=list)
    query_results: list[QueryResult] = field(default_factory=list)
    errors: list[ErrorRecord] = field(default_factory=list)
    metrics: Metrics = field(default_factory=Metrics)
    total_wall_s: float = 0.0
    estimated_cost_usd: float = 0.0
