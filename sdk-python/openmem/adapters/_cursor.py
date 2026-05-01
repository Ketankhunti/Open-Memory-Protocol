"""Opaque pagination cursor codec (M2.1, data-model.md §2a).

Cursor format::

    cursor = base64.urlsafe_b64encode(json.dumps({"page": N}).encode()).decode().rstrip("=")

Used by adapters that paginate by 1-indexed integer page (mem0, supermemory).
Callers MUST treat the cursor string as opaque.

Security
--------
The base64-wrapping is a deliberate **opacity barrier** that lets adapters
detect cursor injection attempts (e.g. a malicious caller crafting
``page=999999`` to exhaust upstream quota). Decode failures or schema
violations raise :class:`InvalidRequestError` BEFORE any upstream call.
"""

from __future__ import annotations

import base64
import binascii
import json

from openmem.errors import InvalidRequestError

# Hard cap on decoded page number to prevent quota-exhaustion attacks via
# crafted cursors. Real pagination never approaches this; legitimate users
# stop iterating long before.
MAX_PAGE_NUMBER = 10_000


def encode_cursor(page: int) -> str:
    """Encode a 1-indexed page number into an opaque cursor string."""
    if not isinstance(page, int) or isinstance(page, bool):
        raise TypeError(f"page must be int, got {type(page).__name__}")
    if page < 1:
        raise ValueError(f"page must be >= 1, got {page}")
    raw = json.dumps({"page": page}, separators=(",", ":")).encode("ascii")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def decode_cursor(cursor: str | None) -> int:
    """Decode an opaque cursor to its page number. ``None``/empty → page 1.

    Raises ``InvalidRequestError`` on any malformed input. The error
    message is INTENTIONALLY generic — leaking decode-failure details
    could help an attacker craft a valid cursor by trial and error.
    """
    if not cursor:
        return 1
    if not isinstance(cursor, str):
        raise InvalidRequestError("malformed cursor")
    # Hard cap on input length: legitimate cursors are ~16 bytes; anything
    # bigger is either confused encoding or a probe payload.
    if len(cursor) > 256:
        raise InvalidRequestError("malformed cursor")
    # Re-pad for urlsafe_b64decode
    padded = cursor + "=" * (-len(cursor) % 4)
    try:
        raw = base64.urlsafe_b64decode(padded.encode("ascii"))
    except (binascii.Error, ValueError, UnicodeEncodeError) as exc:
        raise InvalidRequestError("malformed cursor") from exc
    try:
        payload = json.loads(raw)
    except (ValueError, UnicodeDecodeError) as exc:
        raise InvalidRequestError("malformed cursor") from exc
    if not isinstance(payload, dict) or "page" not in payload:
        raise InvalidRequestError("malformed cursor")
    page = payload["page"]
    if not isinstance(page, int) or isinstance(page, bool):
        raise InvalidRequestError("malformed cursor")
    if page < 1 or page > MAX_PAGE_NUMBER:
        raise InvalidRequestError("malformed cursor")
    return page
