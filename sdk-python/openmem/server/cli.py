"""`omp-server` CLI (PR-B / T048-T049, contracts §10).

Precedence: CLI flag > env var > built-in default. Unknown providers
or missing required configuration exit with status 2 and a stderr
message starting `omp-server: missing config:` (C-CLI-3). On successful
boot the line `omp-server: serving <provider> at http://<host>:<port>`
is printed once to stderr (C-CLI-4).
"""

from __future__ import annotations

import argparse
import os
import sys
from typing import Sequence

from openmem import __version__ as _OMP_VERSION
from openmem.server.config import OmpServerConfig

__all__ = ["main", "build_parser", "config_from_args"]


_DESCRIPTION = (
    "omp-server: HTTP server for Open Memory Protocol providers. "
    "trusted-network deployment only — auth deferred to v0.6+."
)

_EPILOG = (
    "Note: this build has no built-in authentication or rate limiting. "
    "Run behind a reverse proxy (nginx/Caddy) or in a private network only.\n\n"
    "Health endpoint behavior (per provider):\n"
    "  postgres   - acquires a pool connection within 1s\n"
    "  passthrough - HEAD upstream within 2s\n"
    "  mem0/supermemory/letta - returns 200 unconditionally to avoid\n"
    "                           paid API spend on health checks.\n"
)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="omp-server",
        description=_DESCRIPTION,
        epilog=_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        "--version",
        action="version",
        version=f"omp-server {_OMP_VERSION}",
    )
    p.add_argument(
        "--provider",
        choices=["postgres", "passthrough", "mem0", "supermemory", "letta"],
        help="Memory backend to serve (default: $OMP_PROVIDER).",
    )
    p.add_argument(
        "--host",
        default=None,
        help="Bind host (default: $OMP_HOST or 127.0.0.1).",
    )
    p.add_argument(
        "--port",
        type=int,
        default=None,
        help="Bind port (default: $OMP_PORT or 8080).",
    )
    p.add_argument(
        "--max-request-bytes",
        type=int,
        default=None,
        help="Max request body size in bytes (default: 1048576).",
    )
    p.add_argument(
        "--cors-origins",
        default=None,
        help="Comma-separated CORS allowlist (default: empty = disabled).",
    )
    p.add_argument(
        "--log-level",
        default=None,
        choices=["critical", "error", "warning", "info", "debug", "trace"],
        help="uvicorn log level (default: info).",
    )

    # Provider-specific
    p.add_argument(
        "--url",
        default=None,
        help="Postgres URL (provider=postgres). Or set $OMP_POSTGRES_URL.",
    )
    p.add_argument(
        "--base-url",
        default=None,
        help=(
            "Upstream base URL for provider=passthrough. "
            "Or set $OMP_PASSTHROUGH_BASE_URL."
        ),
    )
    return p


def _pick(cli_value, env_key: str, default=None):
    if cli_value is not None:
        return cli_value
    return os.environ.get(env_key, default)


def config_from_args(args: argparse.Namespace) -> OmpServerConfig:
    """Translate parsed args + env into a validated `OmpServerConfig`.

    Raises :class:`SystemExit(2)` (via argparse) for unknown providers,
    or :class:`ValueError` for failed invariants — caller surfaces these
    as `omp-server: missing config: ...` per C-CLI-3.
    """
    provider = _pick(args.provider, "OMP_PROVIDER")
    if not provider:
        raise ValueError("--provider is required (or set $OMP_PROVIDER)")

    cors_raw = _pick(args.cors_origins, "OMP_CORS_ORIGINS", "") or ""
    cors = tuple(c.strip() for c in cors_raw.split(",") if c.strip())

    return OmpServerConfig(
        provider=provider,
        host=_pick(args.host, "OMP_HOST", "127.0.0.1"),
        port=int(_pick(args.port, "OMP_PORT", 8080) or 8080),
        max_request_bytes=int(
            _pick(args.max_request_bytes, "OMP_MAX_REQUEST_BYTES", 1024 * 1024)
            or 1024 * 1024
        ),
        cors_origins=cors,
        log_level=_pick(args.log_level, "OMP_LOG_LEVEL", "info"),
        postgres_url=_pick(args.url, "OMP_POSTGRES_URL"),
        passthrough_base_url=_pick(args.base_url, "OMP_PASSTHROUGH_BASE_URL"),
        mem0_api_key=os.environ.get("MEM0_API_KEY"),
        supermemory_api_key=os.environ.get("SUPERMEMORY_API_KEY"),
        letta_api_key=os.environ.get("LETTA_API_KEY"),
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        cfg = config_from_args(args)
    except ValueError as exc:
        print(f"omp-server: missing config: {exc}", file=sys.stderr)
        return 2

    # Defer FastAPI/uvicorn imports so --help/--version stay snappy.
    try:
        import uvicorn
    except ImportError:
        print(
            "omp-server: missing config: uvicorn not installed. "
            "Run: pip install 'openmem[server]'",
            file=sys.stderr,
        )
        return 2

    from openmem.server.app import create_app

    app = create_app(cfg)
    print(
        f"omp-server: serving {cfg.provider} at http://{cfg.host}:{cfg.port}",
        file=sys.stderr,
        flush=True,
    )
    uvicorn.run(
        app,
        host=cfg.host,
        port=cfg.port,
        log_level=cfg.log_level,
        access_log=False,  # we own access logging via LoggingMiddleware
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
