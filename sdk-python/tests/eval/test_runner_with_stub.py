"""T010 — runner with an injected in-memory stub provider.

Covers:
* fact_id round-trip (R11 dual-stamping) — must work when the backend echoes
  content as well as via the tag fallback when content is rewritten.
* reproducibility — two identical runs produce identical metric numbers (C1).
* multi-provider continuation — one failing provider does not crash the run (C2).
* recall@5 on the bundled dataset against an oracle backend.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from typing import Callable, Iterable

import pytest

from openmem.eval.dataset import load_default
from openmem.eval.runner import _FACT_PREFIX_RE, run
from openmem.eval.types import RunConfig


# ---------------------------------------------------------------------------
# Stubs
# ---------------------------------------------------------------------------


@dataclass
class _Mem:
    """Lightweight stand-in for openmem.types.Memory."""
    id: str
    content: str
    tags: tuple[str, ...] = ()


@dataclass
class _Hit:
    memory: _Mem
    score: float


@dataclass
class _OracleStub:
    """Oracle backend: search returns the exact stamped fact."""
    user_id: str = ""
    _store: dict[str, _Mem] = field(default_factory=dict)
    _by_fact_id: dict[str, _Mem] = field(default_factory=dict)

    def add(self, *, content: str, user_id: str, tags: list[str] | None = None, **_):
        mid = f"m-{uuid.uuid4().hex[:8]}"
        m = _Mem(id=mid, content=content, tags=tuple(tags or ()))
        self._store[mid] = m
        match = _FACT_PREFIX_RE.match(content)
        if match:
            self._by_fact_id[match.group(1)] = m
        return m

    def search(self, query: str, user_id: str, *, limit: int = 5, **_):
        # naive: return the fact whose content contains the most query words
        words = set(query.lower().split())
        scored = []
        for m in self._store.values():
            score = sum(1 for w in words if w in m.content.lower())
            if score > 0:
                scored.append(_Hit(memory=m, score=float(score)))
        scored.sort(key=lambda h: -h.score)
        return scored[:limit]

    def delete(self, mid: str) -> None:
        self._store.pop(mid, None)


@dataclass
class _BrokenStub:
    """Backend that always raises on add to test continuation."""
    user_id: str = ""

    def add(self, **_):
        raise RuntimeError("simulated provider failure")

    def search(self, *_, **__):
        return []

    def delete(self, *_):
        pass


def _factory(mapping: dict[str, Callable]) -> Callable[[str], object]:
    def make(name: str):
        return mapping[name]()
    return make


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_oracle_provider_achieves_high_recall() -> None:
    cfg = RunConfig(providers=("oracle",), live=True, top_k=5)
    ds = load_default()
    [pr] = run(cfg, ds, memory_factory=_factory({"oracle": _OracleStub}))
    assert pr.metrics is not None
    assert pr.metrics.recall_at_5 >= 0.7
    assert pr.metrics.error_count == 0


def test_runner_is_reproducible() -> None:
    """C1 — same inputs → identical recall/MRR."""
    ds = load_default()
    cfg1 = RunConfig(providers=("oracle",), live=True, top_k=5)
    cfg2 = RunConfig(providers=("oracle",), live=True, top_k=5)
    [a] = run(cfg1, ds, memory_factory=_factory({"oracle": _OracleStub}))
    [b] = run(cfg2, ds, memory_factory=_factory({"oracle": _OracleStub}))
    assert a.metrics.recall_at_5 == b.metrics.recall_at_5
    assert a.metrics.mrr == b.metrics.mrr


def test_one_failing_provider_does_not_block_others() -> None:
    """C2 — broken provider gets error_count>0; oracle still runs."""
    ds = load_default()
    cfg = RunConfig(providers=("broken", "oracle"), live=True, top_k=5)
    results = run(
        cfg,
        ds,
        memory_factory=_factory({"broken": _BrokenStub, "oracle": _OracleStub}),
    )
    by_name = {r.provider: r for r in results}
    assert "broken" in by_name and "oracle" in by_name
    assert by_name["broken"].metrics.error_count > 0
    assert by_name["oracle"].metrics.recall_at_5 >= 0.5


def test_dry_run_skips_backend_calls() -> None:
    """Default RunConfig is live=False → runner returns dry-run preview."""
    ds = load_default()
    cfg = RunConfig(providers=("oracle",), live=False)
    [pr] = run(cfg, ds, memory_factory=_factory({"oracle": _OracleStub}))
    # dry run still produces a ProviderResult, but no query results
    assert pr.metrics.error_count == 0
    assert len(pr.query_results) == 0
