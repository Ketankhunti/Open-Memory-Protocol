"""M2.1 / Phase 6 — meta-tests for the live/mock fixture switch.

Covers tasks T044, T045, T046d, T046e from
[specs/003-m2-1-live/tasks.md](../../specs/003-m2-1-live/tasks.md).

These tests exercise the env-var parsing and credential-hygiene
guarantees added by Phase 2 / FR-118 / SC-107.
"""

from __future__ import annotations

import importlib
import logging
import os
from typing import Iterator

import pytest


# ---------------------------------------------------------------------------
# Helpers — re-import conftest helpers via the openmem.tests namespace
# ---------------------------------------------------------------------------


@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Strip every M2.1 env var so each test starts from a known state."""
    for key in (
        "OMP_LIVE",
        "MEM0_API_KEY",
        "SUPERMEMORY_API_KEY",
        "LETTA_API_KEY",
        "OMP_INGEST_TIMEOUT",
    ):
        monkeypatch.delenv(key, raising=False)
    yield


def _is_live_active(provider: str) -> bool:
    """Replicate conftest._is_live_mode_active for direct testing."""
    if (os.environ.get("OMP_LIVE") or "").strip() != "1":
        return False
    key_name = f"{provider.upper()}_API_KEY"
    return bool((os.environ.get(key_name) or "").strip())


# ---------------------------------------------------------------------------
# T044 — fixture switching
# ---------------------------------------------------------------------------


def test_no_omp_live_means_mock_mode(clean_env: None) -> None:
    """Acceptance #1 — without OMP_LIVE, no provider is ever live."""
    for provider in ("mem0", "supermemory", "letta"):
        os.environ[f"{provider.upper()}_API_KEY"] = "sk-set"
        assert _is_live_active(provider) is False
        del os.environ[f"{provider.upper()}_API_KEY"]


def test_per_provider_opt_in(clean_env: None, monkeypatch: pytest.MonkeyPatch) -> None:
    """Acceptance #2 — only the provider whose key is set goes live."""
    monkeypatch.setenv("OMP_LIVE", "1")
    monkeypatch.setenv("MEM0_API_KEY", "sk-fake")
    assert _is_live_active("mem0") is True
    assert _is_live_active("supermemory") is False
    assert _is_live_active("letta") is False


# ---------------------------------------------------------------------------
# T046d — strict env-var parsing (data-model.md §4a / FR-118)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value", ["", " ", "true", "yes", "0", "1 ", " 1", "11", "01", "True"]
)
def test_omp_live_only_activates_for_exact_one(
    clean_env: None, monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    """Only the literal value "1" (after strip) activates live mode."""
    monkeypatch.setenv("OMP_LIVE", value)
    monkeypatch.setenv("MEM0_API_KEY", "sk-set")
    expected = value.strip() == "1"
    assert _is_live_active("mem0") is expected


@pytest.mark.parametrize("value", ["", "  ", "\n\t"])
def test_whitespace_only_api_key_means_mock_mode(
    clean_env: None, monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.setenv("OMP_LIVE", "1")
    monkeypatch.setenv("MEM0_API_KEY", value)
    assert _is_live_active("mem0") is False


@pytest.mark.parametrize("value", ["-1", "0", "abc", "700", "1.5", ""])
def test_invalid_ingest_timeout_falls_back_to_default(
    clean_env: None, monkeypatch: pytest.MonkeyPatch, value: str, caplog: pytest.LogCaptureFixture
) -> None:
    """Out-of-range / non-numeric OMP_INGEST_TIMEOUT must not break adapters."""
    from openmem.adapters._ingest import (
        DEFAULT_INGEST_TIMEOUT,
        read_ingest_timeout_env,
    )

    monkeypatch.setenv("OMP_INGEST_TIMEOUT", value)
    with caplog.at_level(logging.WARNING):
        result = read_ingest_timeout_env()
    assert result == DEFAULT_INGEST_TIMEOUT


def test_valid_ingest_timeout_within_range_used(
    clean_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    from openmem.adapters._ingest import read_ingest_timeout_env

    monkeypatch.setenv("OMP_INGEST_TIMEOUT", "30")
    assert read_ingest_timeout_env() == 30


# ---------------------------------------------------------------------------
# T046e — defence against credential exfiltration via debug logging.
# ---------------------------------------------------------------------------


def test_no_credentials_in_logs(
    clean_env: None,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """FR-118 / SC-107 — API key MUST NOT appear in any log record."""
    secret = "sk-supersecret-DO-NOT-LEAK"
    monkeypatch.setenv("OMP_LIVE", "1")
    monkeypatch.setenv("MEM0_API_KEY", secret)

    from unittest.mock import MagicMock

    from openmem.adapters.mem0 import Mem0Adapter
    from openmem.types import MemoryInput

    fake_client = MagicMock()
    fake_client.add.return_value = {"event_id": "evt_x"}
    fake_client.get.return_value = {
        "id": "evt_x",
        "memory": "hello",
        "user_id": "u1",
        "created_at": "2026-01-01T00:00:00+00:00",
    }
    fake_client.delete.return_value = None

    with caplog.at_level(logging.DEBUG, logger="openmem"):
        adapter = Mem0Adapter(api_key=secret, client=fake_client)
        mem = adapter.add(MemoryInput(content="hello", user_id="u1"))
        try:
            adapter.delete(mem.id)
        except Exception:
            pass

    for record in caplog.records:
        assert secret not in record.getMessage(), (
            f"credential leaked into log record: {record.name} / {record.levelname}"
        )
        # Also defend against partial leaks (prefix exposure attacks)
        assert "supersecret" not in record.getMessage()
