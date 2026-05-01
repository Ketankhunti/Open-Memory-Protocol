"""T014 — trace JSONL writer with hashed payloads."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from openmem.eval.trace import TraceWriter


def test_trace_writes_jsonl_with_payload_hash(tmp_path: Path) -> None:
    out = tmp_path / "t.jsonl"
    with TraceWriter(out) as w:
        w.emit(
            provider="postgres",
            verb="add",
            run_id="r1",
            latency_ms=12.5,
            payload_text="user prefers pnpm",
        )
        w.emit(
            provider="postgres",
            verb="search",
            run_id="r1",
            latency_ms=3.4,
            payload_text="package manager",
            result_count=5,
        )
    lines = out.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    rec0 = json.loads(lines[0])
    assert rec0["provider"] == "postgres"
    assert rec0["verb"] == "add"
    assert "payload_hash" in rec0
    assert len(rec0["payload_hash"]) == 12
    assert "user prefers pnpm" not in lines[0]  # raw text never written
    rec1 = json.loads(lines[1])
    assert rec1["result_count"] == 5


def test_trace_emits_error_field(tmp_path: Path) -> None:
    out = tmp_path / "t.jsonl"
    with TraceWriter(out) as w:
        w.emit(provider="mem0", verb="add", run_id="r1", latency_ms=1.0, error="boom")
    rec = json.loads(out.read_text())
    assert rec["error"] == "boom"


def test_trace_requires_context_manager(tmp_path: Path) -> None:
    w = TraceWriter(tmp_path / "t.jsonl")
    with pytest.raises(RuntimeError):
        w.emit(provider="x", verb="add", run_id="r", latency_ms=1.0)


def test_trace_creates_parent_directories(tmp_path: Path) -> None:
    out = tmp_path / "deep" / "nested" / "t.jsonl"
    with TraceWriter(out) as w:
        w.emit(provider="x", verb="add", run_id="r", latency_ms=1.0)
    assert out.exists()
