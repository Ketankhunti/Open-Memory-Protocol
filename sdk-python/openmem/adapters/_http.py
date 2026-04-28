"""Shared HTTP utilities for OMP adapters.

Used by `PassthroughAdapter` (US2) and the Supermemory translation
adapter (US3). Centralizes the order-of-evaluation rules for decoding
OMP error envelopes from HTTP responses (per
``contracts/passthrough-http.md``).
"""

from __future__ import annotations

import json
from typing import Any

import httpx

from ..errors import InvalidRequestError, OMPError, ProviderError


def make_client(
    base_url: str,
    api_key: str | None = None,
    *,
    transport: httpx.BaseTransport | None = None,
    timeout: float = 30.0,
) -> httpx.Client:
    """Construct a persistent `httpx.Client` for an OMP adapter.

    - Sets `Authorization: Bearer <api_key>` when `api_key` is provided
      (FR-011); the key is never logged.
    - Sets a `User-Agent` so remote servers can identify the SDK.
    - Allows tests to inject a `MockTransport`.
    """
    headers: dict[str, str] = {
        "Accept": "application/json",
        "User-Agent": "openmem-python/0.2.0",
    }
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    return httpx.Client(
        base_url=base_url.rstrip("/"),
        headers=headers,
        timeout=timeout,
        transport=transport,
        follow_redirects=False,  # we handle one-redirect rule manually
    )


def _try_parse_envelope(response: httpx.Response) -> dict[str, Any] | None:
    """Return the parsed body if it looks like an OMP `Error` envelope."""
    try:
        body = response.json()
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(body, dict):
        return None
    err = body.get("error")
    if isinstance(err, dict) and "code" in err:
        return body
    return None


def decode_omp_error(response: httpx.Response, provider: str) -> OMPError:
    """Map an HTTP error response to the appropriate `OMPError` subclass.

    Order per `contracts/passthrough-http.md`:

    1. Body is OMP `Error` envelope → dispatch via
       `OMPError.from_response_dict`.
    2. HTTP 4xx, no envelope → `InvalidRequestError`.
    3. HTTP 5xx, no envelope → `ProviderError`.
    """
    envelope = _try_parse_envelope(response)
    if envelope is not None:
        return OMPError.from_response_dict(envelope, provider=provider)

    snippet = response.text[:200] if response.text else ""
    if 400 <= response.status_code < 500:
        return InvalidRequestError(
            f"HTTP {response.status_code}: {snippet}", provider=provider
        )
    return ProviderError(
        f"HTTP {response.status_code}: {snippet}", provider=provider
    )


def follow_one_redirect(
    client: httpx.Client, response: httpx.Response
) -> httpx.Response:
    """If `response` is a 3xx, follow exactly one redirect.

    Raises `ProviderError` on a redirect loop (second 3xx). Returns
    `response` unchanged for non-3xx (FR / EC-004).
    """
    if not (300 <= response.status_code < 400):
        return response
    location = response.headers.get("location")
    if not location:
        raise ProviderError(
            f"HTTP {response.status_code} redirect without Location header"
        )
    follow = client.request(response.request.method, location)
    if 300 <= follow.status_code < 400:
        raise ProviderError("redirect loop")
    return follow


__all__ = ["make_client", "decode_omp_error", "follow_one_redirect"]
