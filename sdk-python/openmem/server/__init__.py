"""`openmem.server` — FastAPI HTTP server (PR-B / M3.2).

Public surface (lazy-loaded so `OmpServerConfig` is importable without
the FastAPI extras):

* :class:`OmpServerConfig` — frozen dataclass (no FastAPI dependency).
* :func:`create_app` — build a FastAPI app from a config.
* :data:`app` — module-level FastAPI built from environment variables;
  exists for `uvicorn openmem.server:app` workflows.

Importing :func:`create_app` or :data:`app` without the `[server]`
extras raises :class:`ImportError` with the install hint, mirroring the
behavior of `openmem.AsyncMemory` for the `[async]` extras.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from openmem.server.config import OmpServerConfig

__all__ = ["OmpServerConfig", "create_app", "app"]


def _require_fastapi() -> None:
    """Eagerly probe FastAPI; raise the standard install-hint error."""
    try:
        import fastapi  # noqa: F401
    except ImportError as exc:  # pragma: no cover - exercised in bare venv
        raise ImportError(
            "openmem.server requires the server extras. "
            "Install with: pip install 'openmem[server]'"
        ) from exc


def __getattr__(name: str) -> Any:
    if name == "create_app":
        _require_fastapi()
        from openmem.server.app import create_app as _create_app

        return _create_app
    if name == "app":
        _require_fastapi()
        from openmem.server.app import build_app_from_env

        return build_app_from_env()
    raise AttributeError(f"module 'openmem.server' has no attribute {name!r}")


if TYPE_CHECKING:  # pragma: no cover
    from openmem.server.app import create_app  # noqa: F401
