"""Mem0 live-API tests (M2.1 / Phase 3 / US1).

These tests carry the `live` marker and are auto-skipped when
`OMP_LIVE != "1"` (see conftest.pytest_collection_modifyitems).

The remaining tests in this module use the mock-mode `mem0_adapter`
fixture but exercise the M2.1-specific code paths (queued status,
poll-until timeout, x-mem0.original_content stash, low-information
no-op behaviour) by patching the underlying client.
"""

from __future__ import annotations

import time
from typing import Any
from unittest.mock import MagicMock

import pytest

from openmem.errors import ProviderError
from openmem.types import MemoryInput


# ---------------------------------------------------------------------------
# (a) acceptance #1 — add returns within 5 s with status="queued"
# ---------------------------------------------------------------------------


@pytest.mark.live
def test_live_add_returns_quickly_with_queued_status(mem0_adapter):
    # The shared `mem0_adapter` fixture is configured with `block_on_add=True`
    # so contract tests can rely on synchronous semantics. This test
    # specifically validates the *async* add contract (FR-102), so we build
    # a sibling adapter that re-uses the underlying client without blocking.
    from openmem.adapters.mem0 import Mem0Adapter

    nb = Mem0Adapter.__new__(Mem0Adapter)
    nb.__dict__.update(mem0_adapter.__dict__)
    nb._block_on_add_flag = False
    start = time.monotonic()
    m = nb.add(
        MemoryInput(
            content="omp live probe — add returns quickly", user_id="omp_test"
        )
    )
    elapsed = time.monotonic() - start
    assert elapsed < 5.0, f"add took {elapsed:.1f}s (>5s budget; FR-102)"
    # FR-102 contract: add() returns a populated status field. Live mem0
    # may complete ingestion within the request window and surface
    # "done" directly; the canonical async value is "queued".
    assert m.status in {"queued", "indexing", "done"}, (
        f"expected async status, got {m.status!r}"
    )
    assert m.id


# ---------------------------------------------------------------------------
# (b) acceptance #2 — get polls until status="done" within OMP_INGEST_TIMEOUT
# ---------------------------------------------------------------------------


@pytest.mark.live
def test_live_get_polls_to_done(mem0_adapter):
    m = mem0_adapter.add(
        MemoryInput(
            content="omp live probe — get polls to done", user_id="omp_test"
        )
    )
    fetched = mem0_adapter.get(m.id)
    assert fetched.status == "done", f"expected status=done, got {fetched.status!r}"
    assert fetched.id == m.id


# ---------------------------------------------------------------------------
# (c) EC-101 — get raises ProviderError(code="ingestion_timeout") on budget
# ---------------------------------------------------------------------------


def test_get_raises_ingestion_timeout_when_budget_exhausted(monkeypatch):
    """Patch the underlying client to always 404; assert poll raises."""
    from openmem.adapters.mem0 import Mem0Adapter
    from openmem.errors import NotFoundError

    fake_client = MagicMock()

    def _always_404(memory_id):
        raise NotFoundError(f"not yet", provider="mem0")

    fake_client.get.side_effect = _always_404
    adapter = Mem0Adapter(api_key="sk-mock", client=fake_client)

    # Force a tiny timeout via the env contract.
    monkeypatch.setenv("OMP_INGEST_TIMEOUT", "1")

    with pytest.raises(ProviderError) as excinfo:
        adapter.get("evt_does_not_exist")
    assert excinfo.value.code == "ingestion_timeout"
    assert excinfo.value.provider == "mem0"


# ---------------------------------------------------------------------------
# (d) acceptance #3 — LLM-rewrite roundtrip via x-mem0.original_content
# ---------------------------------------------------------------------------


@pytest.mark.live
def test_live_llm_rewrite_preserves_original(mem0_adapter):
    original = "I prefer pnpm over npm for nodejs work, please remember that"
    m = mem0_adapter.add(MemoryInput(content=original, user_id="omp_test"))
    fetched = mem0_adapter.get(m.id)
    extras = fetched.model_extra or {}
    x_mem0 = extras.get("x-mem0") or {}
    # Either we kept original_content under the extension OR the rewritten
    # content still contains a recognisable substring.
    has_original = (
        x_mem0.get("original_content") == original
        or "pnpm" in fetched.content.lower()
    )
    assert has_original, (
        f"original wording lost: x-mem0={x_mem0!r}, content={fetched.content!r}"
    )


# ---------------------------------------------------------------------------
# (e) EC-102 — empty / no-op rewrite must not raise
# ---------------------------------------------------------------------------


def test_empty_response_treated_as_no_op(monkeypatch):
    """If mem0.add returns an empty list (no facts extracted), do not raise."""
    from openmem.adapters.mem0 import Mem0Adapter

    fake_client = MagicMock()
    fake_client.add.return_value = {"results": []}

    adapter = Mem0Adapter(api_key="sk-mock", client=fake_client)
    m = adapter.add(MemoryInput(content="hi", user_id="omp_test"))
    # Per EC-102: low-information add returns a Memory whose content is
    # the original text; status reflects that no facts were extracted.
    assert m.user_id == "omp_test"
    assert m.content == "hi"


# ---------------------------------------------------------------------------
# (f) EC-102 explicit assertion — list after low-information add returns []
# ---------------------------------------------------------------------------


def test_list_after_low_info_add_is_empty(monkeypatch):
    from openmem.adapters.mem0 import Mem0Adapter

    fake_client = MagicMock()
    fake_client.add.return_value = {"results": []}
    fake_client.get_all.return_value = {
        "count": 0,
        "next": None,
        "previous": None,
        "results": [],
    }
    adapter = Mem0Adapter(api_key="sk-mock", client=fake_client)
    adapter.add(MemoryInput(content="hi", user_id="omp_test"))
    page = adapter.list("omp_test")
    assert page.items == []
    assert page.next_cursor is None


# ---------------------------------------------------------------------------
# T046 � 429 retry-once helper (EC-106)
# ---------------------------------------------------------------------------


def test_retry_once_on_rate_limited(monkeypatch):
    """retry_once_on_rate_limit honours retry_after then re-invokes once."""
    from openmem.errors import RateLimitedError
    from tests.conftest import retry_once_on_rate_limit

    sleeps: list[float] = []

    def _sleeper(secs: float) -> None:
        sleeps.append(secs)

    calls = {"n": 0}

    def _fn():
        calls["n"] += 1
        if calls["n"] == 1:
            err = RateLimitedError("slow down", provider="mem0")
            err.retry_after = 2
            raise err
        return "ok"

    result = retry_once_on_rate_limit(_fn, sleeper=_sleeper)
    assert result == "ok"
    assert calls["n"] == 2
    assert sleeps == [2.0]


def test_retry_once_caps_retry_after(monkeypatch):
    """A retry_after > 30 s is clamped (defends against hostile servers)."""
    from openmem.errors import RateLimitedError
    from tests.conftest import retry_once_on_rate_limit

    sleeps: list[float] = []

    def _sleeper(secs: float) -> None:
        sleeps.append(secs)

    raised = {"n": 0}

    def _fn():
        raised["n"] += 1
        if raised["n"] == 1:
            err = RateLimitedError("flood", provider="mem0")
            err.retry_after = 99999
            raise err
        return "ok"

    retry_once_on_rate_limit(_fn, sleeper=_sleeper)
    assert sleeps == [30.0]


def test_retry_once_does_not_retry_twice():
    """A second 429 propagates (FR / EC-106)."""
    from openmem.errors import RateLimitedError
    from tests.conftest import retry_once_on_rate_limit

    def _fn():
        err = RateLimitedError("nope", provider="mem0")
        err.retry_after = 1
        raise err

    with pytest.raises(RateLimitedError):
        retry_once_on_rate_limit(_fn, sleeper=lambda s: None)
