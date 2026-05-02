"""FastAPI exception handlers — 11-row mapping per contracts/http-server.md §3.

The envelope format matches `openmem/types.py::Error`:

    {"error": {"code": "<enum>", "message": "<str>", "type": "<group>"}}

`type` is added so server responses are byte-identical to `Error.model_dump()`
which downstream SDKs already parse via `OMPError.from_response_dict`.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from openmem.errors import (
    InvalidRequestError,
    NotFoundError,
    OMPError,
    ProviderError,
    RateLimitedError,
    ScopeDeniedError,
    UnauthorizedError,
    UnsupportedCapabilityError,
)

__all__ = [
    "register_exception_handlers",
    "PayloadTooLarge",
    "ProviderUnavailable",
    "make_envelope",
]

_LOG = logging.getLogger("openmem.server.errors")


# Server-only sentinel exceptions (not raised by adapters). Raised by
# middleware / health checks; mapped to the same envelope shape.

class PayloadTooLarge(Exception):
    """413 — request body exceeds OmpServerConfig.max_request_bytes (FR-021)."""


class ProviderUnavailable(Exception):
    """503 — pool exhausted / health check failed (FR-019)."""


def make_envelope(
    code: str, message: str, *, type_: str = "provider_error"
) -> dict[str, Any]:
    return {
        "error": {
            "code": code,
            "message": message,
            "type": type_,
        }
    }


# ---------------------------------------------------------- handlers

async def _not_found(_: Request, exc: Exception) -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_404_NOT_FOUND,
        content=make_envelope("not_found", str(exc), type_="not_found"),
    )


async def _invalid_request(_: Request, exc: Exception) -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_400_BAD_REQUEST,
        content=make_envelope("invalid_request", str(exc), type_="invalid"),
    )


async def _validation_error(
    _: Request, exc: RequestValidationError
) -> JSONResponse:
    # Pydantic body validation → 400 invalid_request.
    return JSONResponse(
        status_code=status.HTTP_400_BAD_REQUEST,
        content=make_envelope(
            "invalid_request",
            "request body failed validation",
            type_="invalid",
        ),
    )


async def _unauthorized(_: Request, exc: Exception) -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_401_UNAUTHORIZED,
        content=make_envelope("unauthorized", str(exc), type_="auth"),
    )


async def _scope_denied(_: Request, exc: Exception) -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_403_FORBIDDEN,
        content=make_envelope("scope_denied", str(exc), type_="auth"),
    )


async def _rate_limited(_: Request, exc: Exception) -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        content=make_envelope(
            "rate_limited", str(exc), type_="rate_limited"
        ),
    )


async def _unsupported_capability(_: Request, exc: Exception) -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_405_METHOD_NOT_ALLOWED,
        content=make_envelope(
            "unsupported_capability", str(exc), type_="invalid"
        ),
    )


async def _provider_error(_: Request, exc: ProviderError) -> JSONResponse:
    # ProviderError(code="ingestion_timeout") → 504; everything else → 502.
    if exc.code == "ingestion_timeout":
        return JSONResponse(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            content=make_envelope(
                "ingestion_timeout",
                str(exc),
                type_="provider_error",
            ),
        )
    return JSONResponse(
        status_code=status.HTTP_502_BAD_GATEWAY,
        content=make_envelope(
            exc.code or "provider_error",
            str(exc),
            type_="provider_error",
        ),
    )


async def _payload_too_large(_: Request, exc: Exception) -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
        content=make_envelope(
            "payload_too_large", str(exc), type_="invalid"
        ),
    )


async def _provider_unavailable(_: Request, exc: Exception) -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        content=make_envelope(
            "provider_unavailable",
            str(exc),
            type_="provider_error",
        ),
    )


async def _omp_error_fallback(_: Request, exc: OMPError) -> JSONResponse:
    # Catches any OMPError subclass not explicitly handled above.
    return JSONResponse(
        status_code=status.HTTP_502_BAD_GATEWAY,
        content=make_envelope(
            exc.code or "provider_error",
            str(exc),
            type_=exc.type or "provider_error",
        ),
    )


async def _internal_error(_: Request, exc: Exception) -> JSONResponse:
    # FR-020: never echo arbitrary exception text — could leak secrets.
    _LOG.exception("unhandled exception in route", exc_info=exc)
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content=make_envelope(
            "internal_error",
            "internal server error",
            type_="provider_error",
        ),
    )


def register_exception_handlers(app: FastAPI) -> None:
    """Wire all 11 mappings onto the FastAPI app."""
    # Subclass order matters — FastAPI dispatches on first match.
    app.add_exception_handler(NotFoundError, _not_found)
    app.add_exception_handler(InvalidRequestError, _invalid_request)
    app.add_exception_handler(UnauthorizedError, _unauthorized)
    app.add_exception_handler(ScopeDeniedError, _scope_denied)
    app.add_exception_handler(RateLimitedError, _rate_limited)
    app.add_exception_handler(UnsupportedCapabilityError, _unsupported_capability)
    app.add_exception_handler(ProviderError, _provider_error)
    app.add_exception_handler(OMPError, _omp_error_fallback)

    app.add_exception_handler(PayloadTooLarge, _payload_too_large)
    app.add_exception_handler(ProviderUnavailable, _provider_unavailable)

    app.add_exception_handler(RequestValidationError, _validation_error)
    app.add_exception_handler(Exception, _internal_error)
