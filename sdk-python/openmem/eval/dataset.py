"""Dataset loader and hashing for the eval kit.

Loads bundled or user-supplied JSONL fixtures and computes a stable
``dataset_hash`` (SHA-256, first 12 hex chars) over canonical line content.
"""

from __future__ import annotations

import hashlib
import json
from importlib import resources
from pathlib import Path
from typing import Iterable

from openmem.eval.types import Dataset, Fact, Query


def _read_jsonl(text: str) -> list[dict]:
    out: list[dict] = []
    for lineno, raw in enumerate(text.splitlines(), start=1):
        stripped = raw.strip()
        if not stripped:
            continue
        try:
            out.append(json.loads(stripped))
        except json.JSONDecodeError as exc:
            raise ValueError(f"malformed JSONL on line {lineno}: {exc}") from exc
    return out


def _validate(facts: list[Fact], queries: list[Query]) -> None:
    seen: set[str] = set()
    for f in facts:
        if not f.fact_id:
            raise ValueError("fact_id must be non-empty")
        if not f.content:
            raise ValueError(f"content must be non-empty for {f.fact_id}")
        if f.fact_id in seen:
            raise ValueError(f"duplicate fact_id {f.fact_id!r}")
        seen.add(f.fact_id)
    for q in queries:
        if not q.gold_fact_ids:
            raise ValueError(f"query {q.query_id!r} has empty gold_fact_ids")
        for gid in q.gold_fact_ids:
            if gid not in seen:
                raise ValueError(
                    f"query {q.query_id!r} references unknown fact_id {gid!r}"
                )


def _hash(facts_text: str, queries_text: str) -> str:
    """SHA-256 over canonical line-sorted content; first 12 hex chars."""
    canonical = "\n".join(
        sorted(line.strip() for line in (facts_text + "\n" + queries_text).splitlines() if line.strip())
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:12]


def _build(facts_text: str, queries_text: str) -> Dataset:
    facts = tuple(
        Fact(fact_id=r["fact_id"], content=r["content"], tags=tuple(r.get("tags") or ()))
        for r in _read_jsonl(facts_text)
    )
    queries = tuple(
        Query(
            query_id=r["query_id"],
            query=r["query"],
            gold_fact_ids=tuple(r["gold_fact_ids"]),
        )
        for r in _read_jsonl(queries_text)
    )
    _validate(list(facts), list(queries))
    return Dataset(
        facts=facts,
        queries=queries,
        dataset_hash=_hash(facts_text, queries_text),
    )


def load_default() -> Dataset:
    """Load the bundled `default` dataset."""
    pkg = resources.files("openmem.eval.datasets.default")
    facts_text = (pkg / "facts.jsonl").read_text(encoding="utf-8")
    queries_text = (pkg / "queries.jsonl").read_text(encoding="utf-8")
    return _build(facts_text, queries_text)


def load_path(path: Path) -> Dataset:
    """Load a user-supplied dataset from a directory containing
    ``facts.jsonl`` and ``queries.jsonl``."""
    base = Path(path)
    if not base.exists():
        raise FileNotFoundError(f"dataset path does not exist: {base}")
    facts_path = base / "facts.jsonl"
    queries_path = base / "queries.jsonl"
    if not facts_path.exists() or not queries_path.exists():
        raise FileNotFoundError(
            f"dataset directory must contain facts.jsonl and queries.jsonl: {base}"
        )
    return _build(
        facts_path.read_text(encoding="utf-8"),
        queries_path.read_text(encoding="utf-8"),
    )


def sample(dataset: Dataset, n: int) -> Dataset:
    """Return a new Dataset with the first `n` queries by stable hash order."""
    if n <= 0:
        raise ValueError(f"sample n must be > 0, got {n}")
    if n >= len(dataset.queries):
        return dataset
    ordered = sorted(
        dataset.queries,
        key=lambda q: hashlib.sha256(q.query_id.encode()).hexdigest(),
    )
    return Dataset(
        facts=dataset.facts,
        queries=tuple(ordered[:n]),
        dataset_hash=dataset.dataset_hash,  # facts unchanged → hash stable
    )


def dataset_hash(dataset: Dataset) -> str:
    """Accessor for symmetry; the hash is precomputed on load."""
    return dataset.dataset_hash
