"""OMP error hierarchy.

Each subclass corresponds to one of the enumerated `error.code` values in
the OpenAPI `Error` schema. All errors carry the same standard envelope so
applications can branch on `code` / `type` regardless of which provider
raised them (Constitution Principle II — standard error model).
"""

from __future__ import annotations

from typing import Any, ClassVar


class OMPError(Exception):
    """Base class for every OMP error.

    Attributes mirror the `Error.error` payload defined in the OpenAPI spec:
    `code`, `message`, `type`, `provider`, `request_id`.
    """

    code: ClassVar[str] = "provider_error"
    type: ClassVar[str] = "provider_error"

    def __init__(
        self,
        message: str,
        *,
        provider: str | None = None,
        request_id: str | None = None,
        code: str | None = None,
        type: str | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.provider = provider
        self.request_id = request_id
        # Allow override at construction (used by from_response_dict)
        if code is not None:
            self.code = code  # type: ignore[misc]
        if type is not None:
            self.type = type  # type: ignore[misc]

    def to_envelope(self) -> dict[str, Any]:
        return {
            "error": {
                "code": self.code,
                "message": self.message,
                "type": self.type,
                "provider": self.provider,
                "request_id": self.request_id,
            }
        }

    @classmethod
    def from_response_dict(
        cls, payload: dict[str, Any], provider: str | None = None
    ) -> "OMPError":
        """Reconstruct the right subclass from a wire-format error envelope."""
        err = payload.get("error", payload) if isinstance(payload, dict) else {}
        code = err.get("code", "provider_error")
        message = err.get("message", "")
        request_id = err.get("request_id")
        provider = err.get("provider", provider)
        klass = _CODE_TO_CLASS.get(code, ProviderError)
        return klass(
            message,
            provider=provider,
            request_id=request_id,
            code=code,
        )


class UnauthorizedError(OMPError):
    code = "unauthorized"
    type = "auth"


class ScopeDeniedError(OMPError):
    code = "scope_denied"
    type = "auth"


class NotFoundError(OMPError):
    code = "not_found"
    type = "not_found"


class InvalidRequestError(OMPError):
    code = "invalid_request"
    type = "invalid"


class RateLimitedError(OMPError):
    code = "rate_limited"
    type = "rate_limited"


class UnsupportedCapabilityError(OMPError):
    code = "unsupported_capability"
    type = "invalid"


class ProviderError(OMPError):
    code = "provider_error"
    type = "provider_error"


class UnsupportedProviderError(OMPError):
    """Raised by `_resolve_adapter` when no adapter is registered.

    Not part of the wire spec; SDK-only (the spec doesn't enumerate it
    because, by definition, you never reach a provider to get its error).
    """

    code = "invalid_request"
    type = "invalid"


_CODE_TO_CLASS: dict[str, type[OMPError]] = {
    "unauthorized": UnauthorizedError,
    "scope_denied": ScopeDeniedError,
    "not_found": NotFoundError,
    "invalid_request": InvalidRequestError,
    "rate_limited": RateLimitedError,
    "unsupported_capability": UnsupportedCapabilityError,
    "provider_error": ProviderError,
}


__all__ = [
    "OMPError",
    "UnauthorizedError",
    "ScopeDeniedError",
    "NotFoundError",
    "InvalidRequestError",
    "RateLimitedError",
    "UnsupportedCapabilityError",
    "ProviderError",
    "UnsupportedProviderError",
]
