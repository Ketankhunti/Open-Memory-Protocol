"""Cross-adapter validation helpers (T008 / M3.2).

Centralizes input checks that previously lived inline in each sync
adapter so both sync and the upcoming async adapters share one
implementation.
"""

from __future__ import annotations

from ..errors import InvalidRequestError


def require_user_id(user_id: str | None, *, provider: str) -> str:
    """Raise ``InvalidRequestError`` if ``user_id`` is missing or whitespace.

    Returns the (unchanged) ``user_id`` for ergonomic chaining.

    Cross-user broadening defence: every adapter MUST refuse a search/list
    that omits ``user_id`` BEFORE issuing any upstream call.
    """
    if user_id is None or not str(user_id).strip():
        raise InvalidRequestError("user_id is required", provider=provider)
    return user_id


__all__ = ["require_user_id"]
