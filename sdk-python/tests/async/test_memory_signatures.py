"""Sync `Memory` signature regression — SC-008 / FR-011 backstop (T031).

Stores a JSON snapshot of every public verb's signature on
:class:`openmem.Memory`. Any future change that adds, removes, or
reorders a parameter (or changes a default / annotation textually)
MUST also update the snapshot — making the API contract explicit and
reviewable in a single diff.

The snapshot lives at ``tests/async/_signatures_baseline.json`` and is
auto-created on first run. JSON tuples are used (not pickled
``inspect.Signature``) because pickle output is not stable across
Python micro versions.
"""

from __future__ import annotations

import inspect
import json
from pathlib import Path

import pytest

from openmem import Memory

_BASELINE_PATH = Path(__file__).with_name("_signatures_baseline.json")

# The public surface we lock down — anything callable on `Memory` that
# isn't dunder.
_PUBLIC_NAMES: tuple[str, ...] = (
    "__init__",
    "add",
    "get",
    "update",
    "delete",
    "list",
    "search",
    "context",
    "audit",
    "capabilities",
    "wait_for_ingest",
)


def _sig_payload(fn) -> list[list[str]]:
    sig = inspect.signature(fn)
    out: list[list[str]] = []
    for p in sig.parameters.values():
        out.append(
            [
                p.name,
                p.kind.name,
                "<no-default>"
                if p.default is inspect.Parameter.empty
                else repr(p.default),
                "<no-annotation>"
                if p.annotation is inspect.Parameter.empty
                else str(p.annotation),
            ]
        )
    return out


def _current_snapshot() -> dict[str, list[list[str]]]:
    snapshot: dict[str, list[list[str]]] = {}
    for name in _PUBLIC_NAMES:
        fn = getattr(Memory, name)
        snapshot[name] = _sig_payload(fn)
    return snapshot


def test_memory_signatures_unchanged():
    """Sync `Memory` public signatures MUST match the committed baseline."""
    current = _current_snapshot()

    if not _BASELINE_PATH.exists():
        # First run: persist the baseline and pass. Subsequent runs
        # compare against this committed snapshot.
        _BASELINE_PATH.write_text(
            json.dumps(current, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        pytest.skip(
            f"baseline not present — wrote initial snapshot to "
            f"{_BASELINE_PATH.name}; commit it and re-run"
        )

    baseline = json.loads(_BASELINE_PATH.read_text(encoding="utf-8"))
    assert current == baseline, (
        "Sync `Memory` signature drift detected. If the change is "
        "intentional and backwards compatible, regenerate the snapshot:\n"
        f"  rm {_BASELINE_PATH}\n"
        "  pytest tests/async/test_memory_signatures.py -q\n"
        "  git add tests/async/_signatures_baseline.json\n"
        "Otherwise, revert the breaking change to preserve SC-008 / FR-011."
    )
