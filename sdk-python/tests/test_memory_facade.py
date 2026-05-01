"""Coverage for the user-facing `Memory` facade and the `_scripts` entry point.

These tests use lightweight stub adapters (no real backend) to exercise the
verb-passthrough surface in `openmem/memory.py` and the console-script
helper in `openmem/_scripts.py`.
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from typing import Any
from unittest.mock import patch

import pytest

from openmem import Memory
from openmem.adapters.base import BaseAdapter
from openmem.errors import UnsupportedProviderError, UnsupportedCapabilityError
from openmem.types import (
    AuditEntry,
    Capabilities,
    CapabilityFeatures,
    CapabilityLimits,
    ContextBlock,
    Memory as _MemoryRecord,
    MemoryInput,
    MemoryPage,
    MemorySource,
    MemoryUpdate,
    SearchResult,
    _Citation,
)


# ---------------------------------------------------------------------------
# Stub adapter that records calls so we can assert pass-through faithfulness
# ---------------------------------------------------------------------------


class _StubAdapter(BaseAdapter):
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple, dict]] = []

    def _record(self, name: str, *args: Any, **kwargs: Any) -> None:
        self.calls.append((name, args, kwargs))

    def add(self, memory: MemoryInput) -> _MemoryRecord:
        self._record("add", memory)
        return _MemoryRecord(
            id="mem_stub",
            content=memory.content,
            user_id=memory.user_id,
            scope=memory.scope,
            tags=memory.tags or [],
            created_at=datetime.now(timezone.utc),
        )

    def get(self, id: str) -> _MemoryRecord:
        self._record("get", id)
        return _MemoryRecord(
            id=id,
            content="x",
            user_id="u1",
            created_at=datetime.now(timezone.utc),
        )

    def update(self, id: str, update: MemoryUpdate) -> _MemoryRecord:
        self._record("update", id, update)
        return _MemoryRecord(
            id=id,
            content=update.content or "x",
            user_id="u1",
            created_at=datetime.now(timezone.utc),
        )

    def delete(self, id: str) -> None:
        self._record("delete", id)

    def list(  # noqa: A003
        self,
        user_id: str,
        *,
        scope: str | None = None,
        tag: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        limit: int = 50,
        cursor: str | None = None,
    ) -> MemoryPage:
        self._record(
            "list",
            user_id,
            scope=scope,
            tag=tag,
            since=since,
            until=until,
            limit=limit,
            cursor=cursor,
        )
        return MemoryPage(items=[], next_cursor=None)

    def search(
        self,
        query: str,
        user_id: str,
        *,
        scope: str | None = None,
        limit: int = 10,
        min_score: float | None = None,
    ) -> list[SearchResult]:
        self._record(
            "search", query, user_id, scope=scope, limit=limit, min_score=min_score
        )
        return []

    def context(
        self,
        query: str,
        user_id: str,
        *,
        scope: str | None = None,
        token_budget: int = 500,
    ) -> ContextBlock:
        self._record(
            "context", query, user_id, scope=scope, token_budget=token_budget
        )
        return ContextBlock(text="", citations=[], token_count=0)

    def capabilities(self) -> Capabilities:
        self._record("capabilities")
        return Capabilities(
            provider="stub",
            omp_version="0.1",
            verbs=["add", "get", "update", "delete", "list", "search", "context"],
            features=CapabilityFeatures(),
            limits=CapabilityLimits(),
        )


def _make_memory(stub: _StubAdapter | None = None) -> tuple[Memory, _StubAdapter]:
    stub = stub or _StubAdapter()
    mem = Memory.__new__(Memory)
    mem._adapter = stub  # type: ignore[attr-defined]
    mem._capabilities = None  # type: ignore[attr-defined]
    return mem, stub


# ---------------------------------------------------------------------------
# Verb pass-through
# ---------------------------------------------------------------------------


def test_facade_add_passes_payload_to_adapter() -> None:
    mem, stub = _make_memory()
    out = mem.add(content="hello", user_id="u1", scope="s", tags=["t"])
    assert out.id == "mem_stub"
    name, args, _ = stub.calls[0]
    assert name == "add"
    assert isinstance(args[0], MemoryInput)
    assert args[0].scope == "s"


def test_facade_add_coerces_dict_source_to_memorysource() -> None:
    mem, stub = _make_memory()
    mem.add(
        content="x",
        user_id="u1",
        source={"app": "vscode", "uri": "scheme://x"},
    )
    payload: MemoryInput = stub.calls[0][1][0]
    assert isinstance(payload.source, MemorySource)
    assert payload.source.app == "vscode"


def test_facade_add_passes_extension_kwargs() -> None:
    mem, stub = _make_memory()
    mem.add(content="x", user_id="u1", **{"x-vendor": {"k": 1}})
    payload: MemoryInput = stub.calls[0][1][0]
    extras = payload.model_extra or {}
    assert extras.get("x-vendor") == {"k": 1}


def test_facade_get_passes_id() -> None:
    mem, stub = _make_memory()
    out = mem.get("mem_42")
    assert out.id == "mem_42"
    assert stub.calls[0][0] == "get"


def test_facade_update_builds_memoryupdate() -> None:
    mem, stub = _make_memory()
    mem.update("mem_x", content="new", scope="s2", tags=["t2"], confidence=0.9)
    name, args, _ = stub.calls[0]
    assert name == "update"
    assert args[0] == "mem_x"
    upd: MemoryUpdate = args[1]
    assert upd.content == "new"
    assert upd.scope == "s2"
    assert upd.confidence == 0.9


def test_facade_delete_passes_id() -> None:
    mem, stub = _make_memory()
    mem.delete("mem_x")
    assert stub.calls[0] == ("delete", ("mem_x",), {})


def test_facade_list_forwards_kwargs() -> None:
    mem, stub = _make_memory()
    page = mem.list("u1", scope="s/*", tag="t", limit=10, cursor="c")
    assert page.items == []
    name, args, kwargs = stub.calls[0]
    assert name == "list"
    assert args == ("u1",)
    assert kwargs["scope"] == "s/*"
    assert kwargs["limit"] == 10
    assert kwargs["cursor"] == "c"


def test_facade_search_forwards_kwargs() -> None:
    mem, stub = _make_memory()
    mem.search("q", "u1", scope="s", limit=5, min_score=0.2)
    name, args, kwargs = stub.calls[0]
    assert name == "search"
    assert args == ("q", "u1")
    assert kwargs["scope"] == "s"
    assert kwargs["limit"] == 5
    assert kwargs["min_score"] == 0.2


def test_facade_context_forwards_kwargs() -> None:
    mem, stub = _make_memory()
    ctx = mem.context("q", "u1", scope="s", token_budget=123)
    assert ctx.text == ""
    name, args, kwargs = stub.calls[0]
    assert name == "context"
    assert kwargs["token_budget"] == 123


def test_facade_audit_propagates_unsupported() -> None:
    """audit() default raises UnsupportedCapabilityError on stubs."""
    mem, _ = _make_memory()
    with pytest.raises(UnsupportedCapabilityError):
        mem.audit("u1")


def test_facade_capabilities_caches_result() -> None:
    mem, stub = _make_memory()
    a = mem.capabilities()
    b = mem.capabilities()
    assert a is b  # cached
    capability_calls = [c for c in stub.calls if c[0] == "capabilities"]
    assert len(capability_calls) == 1


def test_facade_wait_for_ingest_is_noop_when_adapter_lacks_hook() -> None:
    """T015a — adapters using BaseAdapter's default no-op succeed silently."""
    mem, _ = _make_memory()
    mem.wait_for_ingest(["m-1", "m-2"], "u1")
    mem.wait_for_ingest(["m-1"], "u1", timeout=5.0)


def test_facade_wait_for_ingest_calls_adapter_hook_when_overridden() -> None:
    """T015a — pass-through invokes adapter.wait_for_ingest with all args."""
    mem, stub = _make_memory()
    captured: list[tuple] = []

    def _hook(ids, user_id, *, timeout=None):
        captured.append((tuple(ids), user_id, timeout))

    stub.wait_for_ingest = _hook  # type: ignore[attr-defined]
    mem.wait_for_ingest(["m-1", "m-2"], "u1", timeout=12.5)
    assert captured == [(("m-1", "m-2"), "u1", 12.5)]


# ---------------------------------------------------------------------------
# _resolve_adapter — provider dispatch and error paths
# ---------------------------------------------------------------------------


def test_resolve_adapter_unknown_provider_raises() -> None:
    from openmem.memory import _resolve_adapter

    with pytest.raises(UnsupportedProviderError):
        _resolve_adapter("totally-unknown")


def test_resolve_adapter_postgres_requires_url() -> None:
    from openmem.memory import _resolve_adapter

    with pytest.raises(ValueError, match="url"):
        _resolve_adapter("postgres")


def test_resolve_adapter_mem0_requires_api_key() -> None:
    from openmem.memory import _resolve_adapter

    with pytest.raises(ValueError, match="api_key"):
        _resolve_adapter("mem0")


def test_resolve_adapter_supermemory_requires_api_key() -> None:
    from openmem.memory import _resolve_adapter

    with pytest.raises(ValueError, match="api_key"):
        _resolve_adapter("supermemory")


def test_resolve_adapter_letta_requires_api_key() -> None:
    from openmem.memory import _resolve_adapter

    with pytest.raises(ValueError, match="api_key"):
        _resolve_adapter("letta")


def test_resolve_adapter_mem0_with_client_stub_succeeds() -> None:
    from unittest.mock import MagicMock

    from openmem.adapters.mem0 import Mem0Adapter
    from openmem.memory import _resolve_adapter

    adapter = _resolve_adapter("mem0", api_key="sk-x", client=MagicMock())
    assert isinstance(adapter, Mem0Adapter)


def test_resolve_adapter_letta_with_client_stub_succeeds() -> None:
    from unittest.mock import MagicMock

    from openmem.adapters.letta import LettaAdapter
    from openmem.memory import _resolve_adapter

    adapter = _resolve_adapter("letta", api_key="sk-x", client=MagicMock())
    assert isinstance(adapter, LettaAdapter)


# ---------------------------------------------------------------------------
# _scripts.validate_spec — happy + missing-file paths
# ---------------------------------------------------------------------------


def test_validate_spec_returns_zero_on_valid_spec(capsys: pytest.CaptureFixture) -> None:
    from openmem._scripts import validate_spec

    rc = validate_spec()
    out = capsys.readouterr().out
    assert rc == 0
    assert "OK:" in out


def test_validate_spec_returns_two_when_spec_missing(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture,
) -> None:
    """If the spec file is moved away, validate_spec returns code 2."""
    import openmem._scripts as scripts
    from pathlib import Path as _RealPath

    real_exists = _RealPath.exists

    def _fake_exists(self: _RealPath) -> bool:
        if self.name == "omp-0.1.openapi.yaml":
            return False
        return real_exists(self)

    monkeypatch.setattr(_RealPath, "exists", _fake_exists)
    rc = scripts.validate_spec()
    err = capsys.readouterr().err
    assert rc == 2
    assert "spec not found" in err


def test_validate_spec_returns_two_when_dev_deps_missing(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture,
) -> None:
    """Missing yaml/openapi-spec-validator triggers a code-2 install hint."""
    import builtins

    real_import = builtins.__import__

    def _fail_yaml(name: str, *args: Any, **kwargs: Any):
        if name in ("yaml", "openapi_spec_validator"):
            raise ImportError(f"no module named {name!r}")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _fail_yaml)
    # Force a fresh import of the symbol so the patched __import__ runs.
    sys.modules.pop("openmem._scripts", None)
    from openmem._scripts import validate_spec

    rc = validate_spec()
    err = capsys.readouterr().err
    assert rc == 2
    assert "missing dev deps" in err
