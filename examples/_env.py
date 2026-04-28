"""Tiny zero-dependency ``.env`` loader.

Loads ``KEY=VALUE`` lines from a ``.env`` file at the repository root
into ``os.environ`` *without* overwriting variables already set in the
process. Lines beginning with ``#`` and blank lines are ignored. Values
may be optionally wrapped in single or double quotes.

Why not ``python-dotenv``? We deliberately avoid adding a runtime
dependency for what is ~30 lines of code; the SDK itself never reads
``.env`` — only examples and tests do.
"""

from __future__ import annotations

import os
from pathlib import Path


def find_env_file(start: Path | None = None) -> Path | None:
    """Walk upwards from ``start`` (or CWD) looking for a ``.env`` file."""
    cur = (start or Path.cwd()).resolve()
    for parent in (cur, *cur.parents):
        candidate = parent / ".env"
        if candidate.is_file():
            return candidate
    return None


def load_env(path: Path | None = None, *, override: bool = False) -> int:
    """Populate ``os.environ`` from a ``.env`` file. Returns the number of
    keys loaded. Silently does nothing if no file is found."""
    target = path or find_env_file()
    if target is None:
        return 0
    loaded = 0
    for raw in target.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if (len(value) >= 2) and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        if not key:
            continue
        if not override and key in os.environ:
            continue
        os.environ[key] = value
        loaded += 1
    return loaded


__all__ = ["find_env_file", "load_env"]
