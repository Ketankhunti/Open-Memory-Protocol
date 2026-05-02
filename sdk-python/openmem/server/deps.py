"""FastAPI dependencies — `AsyncMemory` accessor and user_id extraction."""

from __future__ import annotations

from typing import Any

from fastapi import Header, HTTPException, Request, status

from openmem.async_memory import AsyncMemory
from openmem.errors import InvalidRequestError

__all__ = ["get_memory", "extract_user_id_from_header", "extract_user_id_from_body"]


def get_memory(request: Request) -> AsyncMemory:
    """Return the singleton `AsyncMemory` attached to `app.state.memory`.

    Raised at import-time issues are surfaced as 503 (server misconfigured).
    """
    mem: AsyncMemory | None = getattr(request.app.state, "memory", None)
    if mem is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="memory backend not initialized",
        )
    return mem


def _validate_user_id(value: str | None) -> str:
    """C-UID-2: reject missing/empty/whitespace user_id BEFORE adapter call."""
    if value is None or not str(value).strip():
        raise InvalidRequestError("user_id must be a non-empty string")
    return str(value)


async def extract_user_id_from_header(
    x_user_id: str | None = Header(default=None, alias="X-User-Id"),
) -> str:
    """Used by GET/DELETE routes that have no JSON body."""
    return _validate_user_id(x_user_id)


def extract_user_id_from_body(payload: dict[str, Any]) -> str:
    """Used inside POST/PATCH route bodies (Pydantic body has user_id)."""
    return _validate_user_id(payload.get("user_id"))
