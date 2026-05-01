"""T017 + I1 + FR-011 — CLI flag parsing, dry-run default, CI refusal."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from openmem.eval import cli as cli_mod


def test_dry_run_is_default_and_writes_report(tmp_path: Path, monkeypatch) -> None:
    """FR-003 — without --live we render a dry-run preview, no provider calls."""
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    report = tmp_path / "r.md"
    rc = cli_mod.main(
        [
            "--providers", "postgres,mem0",
            "--report", str(report),
            "--trace", str(tmp_path / "t.jsonl"),
        ]
    )
    assert rc == 0
    text = report.read_text(encoding="utf-8")
    assert "dry-run" in text
    assert "postgres" in text
    assert "mem0" in text


def test_no_providers_returns_exit_1(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.delenv("CI", raising=False)
    rc = cli_mod.main(["--providers", "", "--report", str(tmp_path / "r.md")])
    assert rc == 1


def test_refuses_to_run_live_in_ci(monkeypatch, tmp_path: Path) -> None:
    """FR-011 — CI=true with --live exits 4."""
    monkeypatch.setenv("CI", "true")
    rc = cli_mod.main(
        [
            "--providers", "postgres",
            "--live",
            "--report", str(tmp_path / "r.md"),
        ]
    )
    assert rc == 4


def test_dataset_error_returns_exit_4(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.delenv("CI", raising=False)
    rc = cli_mod.main(
        [
            "--providers", "postgres",
            "--report", str(tmp_path / "r.md"),
            "--dataset", str(tmp_path / "does-not-exist"),
        ]
    )
    assert rc == 4


def test_sample_flag_limits_queries(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.delenv("CI", raising=False)
    report = tmp_path / "r.md"
    rc = cli_mod.main(
        [
            "--providers", "postgres",
            "--sample", "3",
            "--report", str(report),
        ]
    )
    assert rc == 0
    assert "Sample" in report.read_text(encoding="utf-8")


def test_cost_confirmation_refusal_returns_exit_3(monkeypatch, tmp_path: Path) -> None:
    """FR-004 — exceeding threshold without --yes prompts; declining → exit 3."""
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    monkeypatch.setattr(cli_mod, "_confirm", lambda _msg: False)
    rc = cli_mod.main(
        [
            "--providers", "mem0,letta",
            "--live",
            "--cost-threshold", "0.0001",
            "--report", str(tmp_path / "r.md"),
        ]
    )
    assert rc == 3


def test_cost_confirmation_yes_flag_skips_prompt(monkeypatch, tmp_path: Path) -> None:
    """--yes bypasses the prompt; we still won't actually run a real provider
    because we monkeypatch the runner to return an empty result list."""
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)

    captured = {}

    def fake_run(cfg, dataset, **_kw):
        captured["live"] = cfg.live
        from openmem.eval.types import Metrics, ProviderResult, QueryResult
        return [
            ProviderResult(
                provider=p,
                status="ok",
                metrics=Metrics(),
                query_results=[QueryResult(query_id="q1", top_k_fact_ids=["x"])],
            )
            for p in cfg.providers
        ]

    monkeypatch.setattr(cli_mod, "run_eval", fake_run)
    rc = cli_mod.main(
        [
            "--providers", "mem0",
            "--live",
            "--yes",
            "--cost-threshold", "0.0001",
            "--report", str(tmp_path / "r.md"),
        ]
    )
    assert rc == 0
    assert captured["live"] is True


def test_help_exits_two() -> None:
    """argparse exits 2 on bad invocation; --help exits 0."""
    with pytest.raises(SystemExit) as exc:
        cli_mod.main(["--help"])
    assert exc.value.code == 0


def test_version_flag_prints_and_exits_zero(capsys) -> None:
    rc = cli_mod.main(["--version"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "openmem" in out
    assert "dataset default-" in out


def test_dry_run_and_live_are_mutually_exclusive(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv("CI", raising=False)
    rc = cli_mod.main(
        ["--providers", "postgres", "--dry-run", "--live", "--report", str(tmp_path / "r.md")]
    )
    assert rc == 2


def test_dry_run_prints_per_provider_cost_table(monkeypatch, tmp_path, capsys) -> None:
    monkeypatch.delenv("CI", raising=False)
    rc = cli_mod.main(
        [
            "--providers", "postgres,mem0",
            "--report", str(tmp_path / "r.md"),
        ]
    )
    assert rc == 0
    out = capsys.readouterr().out
    assert "DRY RUN" in out
    assert "postgres" in out
    assert "mem0" in out
    assert "TOTAL" in out


def test_sample_annotation_in_report(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv("CI", raising=False)
    report = tmp_path / "r.md"
    rc = cli_mod.main(
        ["--providers", "postgres", "--sample", "4", "--report", str(report)]
    )
    assert rc == 0
    text = report.read_text(encoding="utf-8")
    assert "sample=4" in text
