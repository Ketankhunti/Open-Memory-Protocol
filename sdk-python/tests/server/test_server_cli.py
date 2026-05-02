"""CLI tests (T039 / contracts §10 — C-CLI-1..3).

Boot/serve (C-CLI-4) is exercised end-to-end in `test_throughput_bench.py`
under `OMP_LIVE=1`; we keep this file fast by exercising argparse only.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from openmem.server import cli as server_cli

# Repo root for subprocess invocation.
_REPO_ROOT = Path(__file__).resolve().parents[3]


def _run_cli(*args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    """Invoke `python -m openmem.server.cli ...` (so we don't depend on PATH)."""
    full_env = {**os.environ, **(env or {})}
    return subprocess.run(
        [sys.executable, "-m", "openmem.server.cli", *args],
        env=full_env,
        capture_output=True,
        text=True,
        cwd=_REPO_ROOT,
        timeout=30,
    )


def test_help_contains_required_phrases():
    """C-CLI-1: --help contains 'trusted-network deployment only' and 'auth deferred'."""
    r = _run_cli("--help")
    assert r.returncode == 0
    out = r.stdout + r.stderr
    assert "trusted-network deployment only" in out
    assert "auth deferred" in out


def test_version_exits_zero():
    """C-CLI-2: --version prints `omp-server <pkg-version>` and exits 0."""
    from openmem import __version__

    r = _run_cli("--version")
    assert r.returncode == 0
    out = r.stdout + r.stderr
    assert "omp-server" in out
    assert __version__ in out


def test_missing_provider_exits_2():
    """C-CLI-3: missing required config exits 2 with stderr prefix."""
    # Strip any OMP_PROVIDER from the inherited env.
    env = {k: v for k, v in os.environ.items() if not k.startswith("OMP_")}
    r = subprocess.run(
        [sys.executable, "-m", "openmem.server.cli"],
        env=env,
        capture_output=True,
        text=True,
        cwd=_REPO_ROOT,
        timeout=30,
    )
    assert r.returncode == 2
    assert r.stderr.startswith("omp-server: missing config:")


def test_missing_postgres_url_exits_2():
    """provider=postgres without --url/$OMP_POSTGRES_URL → exit 2."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("OMP_")}
    r = subprocess.run(
        [sys.executable, "-m", "openmem.server.cli", "--provider", "postgres"],
        env=env,
        capture_output=True,
        text=True,
        cwd=_REPO_ROOT,
        timeout=30,
    )
    assert r.returncode == 2
    assert r.stderr.startswith("omp-server: missing config:")
    assert "postgres_url" in r.stderr


# -------------------------------------------------- in-process arg parsing

def test_config_from_args_uses_cli_over_env(monkeypatch):
    monkeypatch.setenv("OMP_PROVIDER", "passthrough")
    monkeypatch.setenv("OMP_PASSTHROUGH_BASE_URL", "http://from-env")
    parser = server_cli.build_parser()
    args = parser.parse_args(
        ["--provider", "postgres", "--url", "postgresql://from-cli"]
    )
    cfg = server_cli.config_from_args(args)
    assert cfg.provider == "postgres"
    assert cfg.postgres_url == "postgresql://from-cli"


def test_config_from_args_falls_back_to_env(monkeypatch):
    monkeypatch.setenv("OMP_PROVIDER", "passthrough")
    monkeypatch.setenv("OMP_PASSTHROUGH_BASE_URL", "http://from-env")
    monkeypatch.setenv("OMP_PORT", "9999")
    parser = server_cli.build_parser()
    args = parser.parse_args([])
    cfg = server_cli.config_from_args(args)
    assert cfg.provider == "passthrough"
    assert cfg.passthrough_base_url == "http://from-env"
    assert cfg.port == 9999
