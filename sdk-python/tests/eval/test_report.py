"""T009 — Markdown report writer."""

from __future__ import annotations

from pathlib import Path

import pytest

from openmem.eval.report import write_report
from openmem.eval.types import (
    Metrics,
    ProviderResult,
    QueryResult,
    RunConfig,
)


def _make_provider(
    name: str,
    *,
    recall_at_5: float = 0.8,
    estimated_cost_usd: float = 0.0123,
    error_count: int = 0,
    note: str | None = None,
) -> ProviderResult:
    metrics = Metrics(
        recall_at_1=recall_at_5 / 2,
        recall_at_5=recall_at_5,
        mrr=recall_at_5,
        ingest_p50_ms=10.0,
        ingest_p95_ms=20.0,
        ingest_p99_ms=25.0,
        search_p50_ms=5.0,
        search_p95_ms=8.0,
        search_p99_ms=10.0,
        error_count=error_count,
        note=note,
    )
    return ProviderResult(
        provider=name,
        query_results=[QueryResult(query_id="q1", top_k_fact_ids=["a"])],
        metrics=metrics,
        estimated_cost_usd=estimated_cost_usd,
    )


def test_write_report_creates_file_with_header(tmp_path: Path) -> None:
    out = tmp_path / "report.md"
    cfg = RunConfig(providers=("stub",), live=False)
    write_report(out, cfg, [_make_provider("stub")], dataset_hash="abc123")
    text = out.read_text(encoding="utf-8")
    assert "# OMP Eval Report" in text
    assert cfg.run_id in text
    assert "abc123" in text


def test_report_contains_provider_metrics_table(tmp_path: Path) -> None:
    out = tmp_path / "r.md"
    cfg = RunConfig(providers=("postgres", "mem0"), live=True)
    write_report(
        out,
        cfg,
        [
            _make_provider("postgres", recall_at_5=0.92, estimated_cost_usd=0.0),
            _make_provider("mem0", recall_at_5=0.7, estimated_cost_usd=0.42),
        ],
        dataset_hash="h1",
    )
    text = out.read_text(encoding="utf-8")
    assert "postgres" in text
    assert "mem0" in text
    assert "0.92" in text
    assert "0.7" in text
    # cost column present
    assert "$" in text


def test_report_marks_cache_hits(tmp_path: Path) -> None:
    """Cache feature was dropped — placeholder test removed."""
    pytest.skip("caching not supported")


def test_report_renders_provider_note(tmp_path: Path) -> None:
    out = tmp_path / "r.md"
    cfg = RunConfig(providers=("stub",), live=False)
    write_report(
        out,
        cfg,
        [_make_provider("stub", note="suspicious recall")],
        dataset_hash="h",
    )
    assert "suspicious" in out.read_text(encoding="utf-8").lower()


def test_report_includes_dry_run_or_live_label(tmp_path: Path) -> None:
    out = tmp_path / "r.md"
    cfg_dry = RunConfig(providers=("stub",), live=False)
    write_report(out, cfg_dry, [_make_provider("stub")], dataset_hash="h")
    assert "dry" in out.read_text(encoding="utf-8").lower()

    out2 = tmp_path / "r2.md"
    cfg_live = RunConfig(providers=("stub",), live=True)
    write_report(out2, cfg_live, [_make_provider("stub")], dataset_hash="h")
    assert "live" in out2.read_text(encoding="utf-8").lower()
