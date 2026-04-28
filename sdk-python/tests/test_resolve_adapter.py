"""Tests for `_resolve_adapter` (SPEC §11a auto-detection)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from openmem.errors import UnsupportedProviderError
from openmem.types import CapabilityFeatures


def _make_caps_payload() -> dict:
    return {
        "omp_version": "0.1",
        "provider": "x",
        "verbs": ["add", "search"],
        "features": CapabilityFeatures().model_dump(),
    }


def test_passthrough_used_when_capabilities_returns_omp_version():
    from openmem.adapters.passthrough import PassthroughAdapter
    from openmem.memory import _resolve_adapter

    fake_resp = MagicMock(status_code=200)
    fake_resp.json.return_value = _make_caps_payload()
    fake_resp.raise_for_status = MagicMock()
    with patch("httpx.get", return_value=fake_resp):
        adapter = _resolve_adapter("anything", base_url="https://example.test")
    assert isinstance(adapter, PassthroughAdapter)


def test_translation_used_when_no_omp_version(pg_url):
    """No base_url → fall through to TRANSLATION_ADAPTERS."""
    from openmem.adapters.postgres import PostgresAdapter
    from openmem.memory import _resolve_adapter

    adapter = _resolve_adapter("postgres", url=pg_url)
    assert isinstance(adapter, PostgresAdapter)


def test_unsupported_provider_raises():
    from openmem.memory import _resolve_adapter

    with pytest.raises((UnsupportedProviderError, ValueError)):
        _resolve_adapter("totally-unknown")


def test_capability_probe_is_cached():
    from openmem.memory import Memory

    fake_resp = MagicMock(status_code=200)
    fake_resp.json.return_value = _make_caps_payload()
    fake_resp.raise_for_status = MagicMock()
    with patch("httpx.get", return_value=fake_resp) as mock_get:
        mem = Memory(provider="passthrough", base_url="https://example.test")
        mem.capabilities()
        mem.capabilities()
        # Probe ran exactly once during construction; subsequent calls are cached
        assert mock_get.call_count == 1


def test_probe_returns_none_when_omp_version_missing():
    """C5: direct unit test of PassthroughAdapter._probe."""
    from openmem.adapters.passthrough import PassthroughAdapter

    fake_resp = MagicMock(status_code=200)
    fake_resp.json.return_value = {"provider": "x"}  # no omp_version
    fake_resp.raise_for_status = MagicMock()
    with patch("httpx.get", return_value=fake_resp):
        assert PassthroughAdapter._probe("https://example.test") is None
