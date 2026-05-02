"""HTTP server config (PR-B / data-model §3 + contracts §10).

`OmpServerConfig` is a frozen dataclass capturing CLI args + env defaults
for `omp-server`. Validation enforces the four CFG-INV-1..4 invariants:

* port must be in 1..65535
* max_request_bytes must be in 1024..104_857_600 (1 KiB..100 MiB)
* postgres provider requires `postgres_url`
* mem0/supermemory/letta require the matching `<provider>_api_key`

The dataclass is frozen so a hostile component (e.g. a misbehaving
middleware) cannot mutate it post-construction.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

__all__ = ["OmpServerConfig", "Provider"]

Provider = Literal["postgres", "passthrough", "mem0", "supermemory", "letta"]
_PROVIDERS: tuple[str, ...] = (
    "postgres",
    "passthrough",
    "mem0",
    "supermemory",
    "letta",
)
_API_KEY_PROVIDERS: frozenset[str] = frozenset({"mem0", "supermemory", "letta"})

_MIN_PORT, _MAX_PORT = 1, 65535
_MIN_REQUEST_BYTES = 1024
_MAX_REQUEST_BYTES = 100 * 1024 * 1024  # 100 MiB


@dataclass(frozen=True)
class OmpServerConfig:
    """Validated configuration for a single `omp-server` process.

    All fields default to safe trusted-network values. Invariants are
    checked in `__post_init__` and raise `ValueError` on violation.
    """

    provider: str
    host: str = "127.0.0.1"
    port: int = 8080
    max_request_bytes: int = 1024 * 1024  # 1 MiB default per FR-021
    cors_origins: tuple[str, ...] = field(default_factory=tuple)
    log_level: str = "info"

    # Provider-specific (only the matching one needs to be set).
    postgres_url: str | None = None
    passthrough_base_url: str | None = None
    mem0_api_key: str | None = None
    supermemory_api_key: str | None = None
    letta_api_key: str | None = None

    def __post_init__(self) -> None:
        # Provider must be one of the 5 supported names.
        if self.provider not in _PROVIDERS:
            raise ValueError(
                f"OmpServerConfig: provider must be one of "
                f"{sorted(_PROVIDERS)}, got {self.provider!r}"
            )

        # CFG-INV-1
        if not (_MIN_PORT <= int(self.port) <= _MAX_PORT):
            raise ValueError(
                f"OmpServerConfig: port must be in "
                f"[{_MIN_PORT}, {_MAX_PORT}], got {self.port!r}"
            )

        # CFG-INV-2
        if not (
            _MIN_REQUEST_BYTES
            <= int(self.max_request_bytes)
            <= _MAX_REQUEST_BYTES
        ):
            raise ValueError(
                f"OmpServerConfig: max_request_bytes must be in "
                f"[{_MIN_REQUEST_BYTES}, {_MAX_REQUEST_BYTES}], "
                f"got {self.max_request_bytes!r}"
            )

        # CFG-INV-3
        if self.provider == "postgres" and not (
            self.postgres_url and self.postgres_url.strip()
        ):
            raise ValueError(
                "OmpServerConfig: provider=postgres requires non-empty "
                "postgres_url (set --url or OMP_POSTGRES_URL)"
            )

        # passthrough requires base_url (mirrors sync passthrough adapter).
        if self.provider == "passthrough" and not (
            self.passthrough_base_url and self.passthrough_base_url.strip()
        ):
            raise ValueError(
                "OmpServerConfig: provider=passthrough requires non-empty "
                "passthrough_base_url (set --base-url or OMP_PASSTHROUGH_BASE_URL)"
            )

        # CFG-INV-4
        if self.provider in _API_KEY_PROVIDERS:
            attr = f"{self.provider}_api_key"
            value = getattr(self, attr)
            if not (value and str(value).strip()):
                raise ValueError(
                    f"OmpServerConfig: provider={self.provider} requires "
                    f"non-empty {attr} (set the matching env var)"
                )

        # Normalize cors_origins to a tuple (frozen-friendly) and reject
        # blank entries. Empty tuple = CORS disabled (default-deny per
        # contracts §5 / C-CORS-1).
        if not isinstance(self.cors_origins, tuple):
            object.__setattr__(
                self, "cors_origins", tuple(self.cors_origins)
            )
        for origin in self.cors_origins:
            if not (origin and origin.strip()):
                raise ValueError(
                    "OmpServerConfig: cors_origins entries must be "
                    "non-empty strings"
                )

    # ------------------------------------------------------- helpers

    def adapter_kwargs(self) -> dict[str, object]:
        """Return the **kwargs to pass to AsyncMemory(provider=...).

        Only the provider-specific keys for the configured provider are
        included; everything else stays at its `AsyncMemory` default.
        """
        if self.provider == "postgres":
            return {"url": self.postgres_url}
        if self.provider == "passthrough":
            return {"base_url": self.passthrough_base_url}
        if self.provider == "mem0":
            return {"api_key": self.mem0_api_key}
        if self.provider == "supermemory":
            return {"api_key": self.supermemory_api_key}
        if self.provider == "letta":
            return {"api_key": self.letta_api_key}
        # Unreachable — provider validated in __post_init__.
        raise AssertionError(f"unhandled provider: {self.provider!r}")
