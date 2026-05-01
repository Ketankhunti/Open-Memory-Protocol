"""JSONL trace writer with hashed payloads (privacy-by-default).

Each line is a single JSON object describing one verb call. Payload
content is replaced with a 12-hex SHA-256 prefix so traces are safe to
share without leaking memory bodies.
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any, IO


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


class TraceWriter:
    """Append-only JSONL writer. Use as a context manager."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._fp: IO[str] | None = None

    def __enter__(self) -> "TraceWriter":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fp = self.path.open("w", encoding="utf-8")
        return self

    def __exit__(self, *_exc: object) -> None:
        if self._fp is not None:
            self._fp.close()
            self._fp = None

    def emit(
        self,
        *,
        provider: str,
        verb: str,
        run_id: str,
        latency_ms: float,
        payload_text: str | None = None,
        result_count: int | None = None,
        error: str | None = None,
        extra: dict[str, Any] | None = None,
    ) -> None:
        if self._fp is None:
            raise RuntimeError("TraceWriter must be used inside a `with` block")
        record: dict[str, Any] = {
            "ts": time.time(),
            "run_id": run_id,
            "provider": provider,
            "verb": verb,
            "latency_ms": round(latency_ms, 3),
        }
        if payload_text is not None:
            record["payload_hash"] = _hash(payload_text)
        if result_count is not None:
            record["result_count"] = result_count
        if error is not None:
            record["error"] = error
        if extra:
            record.update(extra)
        self._fp.write(json.dumps(record, separators=(",", ":")) + "\n")


__all__ = ["TraceWriter"]
