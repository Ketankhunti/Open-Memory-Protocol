"""T007 + T025 — dataset loader, hashing, sample()."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from openmem.eval.dataset import (
    dataset_hash,
    load_default,
    load_path,
    sample,
)


def test_load_default_returns_at_least_50_facts_and_20_queries() -> None:
    ds = load_default()
    assert len(ds.facts) >= 50
    assert len(ds.queries) >= 20


def test_dataset_hash_is_deterministic_across_loads() -> None:
    a = load_default()
    b = load_default()
    assert a.dataset_hash == b.dataset_hash
    assert dataset_hash(a) == a.dataset_hash


def test_dataset_hash_changes_when_content_changes(tmp_path: Path) -> None:
    base = tmp_path / "ds"
    base.mkdir()
    (base / "facts.jsonl").write_text(
        '{"fact_id": "f-1", "content": "hello"}\n', encoding="utf-8"
    )
    (base / "queries.jsonl").write_text(
        '{"query_id": "q-1", "query": "hi", "gold_fact_ids": ["f-1"]}\n',
        encoding="utf-8",
    )
    h1 = load_path(base).dataset_hash

    (base / "facts.jsonl").write_text(
        '{"fact_id": "f-1", "content": "goodbye"}\n', encoding="utf-8"
    )
    h2 = load_path(base).dataset_hash
    assert h1 != h2


def test_unknown_gold_fact_id_raises_clear_error(tmp_path: Path) -> None:
    base = tmp_path / "ds"
    base.mkdir()
    (base / "facts.jsonl").write_text(
        '{"fact_id": "f-1", "content": "x"}\n', encoding="utf-8"
    )
    (base / "queries.jsonl").write_text(
        '{"query_id": "q-1", "query": "hi", "gold_fact_ids": ["does-not-exist"]}\n',
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="unknown fact_id"):
        load_path(base)


def test_duplicate_fact_id_raises(tmp_path: Path) -> None:
    base = tmp_path / "ds"
    base.mkdir()
    (base / "facts.jsonl").write_text(
        '{"fact_id": "f-1", "content": "a"}\n{"fact_id": "f-1", "content": "b"}\n',
        encoding="utf-8",
    )
    (base / "queries.jsonl").write_text("", encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate fact_id"):
        load_path(base)


def test_load_path_missing_directory_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_path(tmp_path / "nope")


def test_load_path_missing_files_raises(tmp_path: Path) -> None:
    base = tmp_path / "ds"
    base.mkdir()
    with pytest.raises(FileNotFoundError):
        load_path(base)


def test_malformed_jsonl_raises(tmp_path: Path) -> None:
    base = tmp_path / "ds"
    base.mkdir()
    (base / "facts.jsonl").write_text("not json\n", encoding="utf-8")
    (base / "queries.jsonl").write_text("", encoding="utf-8")
    with pytest.raises(ValueError, match="malformed JSONL"):
        load_path(base)


# ---------------------------------------------------------------------------
# T025 — sample()
# ---------------------------------------------------------------------------


def test_sample_returns_exactly_n_queries() -> None:
    ds = load_default()
    sub = sample(ds, 5)
    assert len(sub.queries) == 5
    # facts unchanged
    assert sub.facts == ds.facts


def test_sample_is_deterministic() -> None:
    ds = load_default()
    a = sample(ds, 7)
    b = sample(ds, 7)
    assert [q.query_id for q in a.queries] == [q.query_id for q in b.queries]


def test_sample_n_larger_than_total_returns_all() -> None:
    ds = load_default()
    sub = sample(ds, 9999)
    assert sub.queries == ds.queries


def test_sample_zero_raises() -> None:
    ds = load_default()
    with pytest.raises(ValueError):
        sample(ds, 0)
