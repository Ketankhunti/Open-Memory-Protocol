"""Cleanup: delete every memory written by a given run_id.

Used by `--cleanup` (FR-015). The harness wrote facts under
``user_id = f"eval-{run_id}"`` so we list and delete them.
"""

from __future__ import annotations

from typing import Any


def cleanup(memory: Any, *, user_id: str) -> int:
    """Delete every memory belonging to `user_id`. Returns count deleted."""
    deleted = 0
    cursor: str | None = None
    while True:
        page = memory.list(user_id, limit=200, cursor=cursor)
        for item in page.items:
            try:
                memory.delete(item.id)
                deleted += 1
            except Exception:  # pragma: no cover - best-effort
                pass
        cursor = getattr(page, "next_cursor", None)
        if not cursor:
            break
    return deleted


__all__ = ["cleanup"]
