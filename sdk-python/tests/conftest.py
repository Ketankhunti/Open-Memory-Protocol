"""Pytest fixtures for the OMP conformance suite.

A session-scoped pgvector container backs every adapter under test. The
``adapter`` fixture is parametrized so adding a new adapter (M2:
``mem0_adapter``, ``supermemory_adapter``, etc.) requires only one new
entry in ``params`` — the contract tests themselves never change.
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterator
from datetime import datetime
from typing import Any

import httpx
import pytest

# Use an env var so CI without docker can fall back to a real Postgres URL.
_USE_TESTCONTAINER = os.environ.get("PG_URL") is None


@pytest.fixture(scope="session")
def pg_url() -> Iterator[str]:
    """Provide a Postgres+pgvector URL.

    Prefers ``$PG_URL`` if set (CI / local Postgres). Otherwise spins up a
    ``pgvector/pgvector:pg16`` testcontainer.
    """
    if not _USE_TESTCONTAINER:
        yield os.environ["PG_URL"]
        return

    try:
        from testcontainers.postgres import PostgresContainer  # type: ignore
    except ImportError:
        pytest.skip(
            "testcontainers not installed; set PG_URL or `pip install openmem[dev]`"
        )

    container: Any = PostgresContainer("pgvector/pgvector:pg16")
    container.start()
    try:
        yield container.get_connection_url().replace(
            "postgresql+psycopg2://", "postgresql://"
        )
    finally:
        container.stop()


@pytest.fixture(scope="module")
def postgres_adapter(pg_url: str):
    """Concrete PostgresAdapter with FakeEmbedder."""
    from openmem.adapters.embedder import FakeEmbedder
    from openmem.adapters.postgres import PostgresAdapter

    return PostgresAdapter(url=pg_url, embedder=FakeEmbedder())


@pytest.fixture(params=["postgres", "passthrough", "mem0", "supermemory", "letta"])
def adapter(
    request,
    postgres_adapter,
    passthrough_adapter,
    mem0_adapter,
    supermemory_adapter,
    letta_adapter,
):
    """Parametrized adapter fixture.

    To add a new adapter:
      1. Add a fixture (e.g. ``mem0_adapter``) above.
      2. Append its name to ``params`` here and to the dispatch dict below.
    """
    dispatch = {
        "postgres": postgres_adapter,
        "passthrough": passthrough_adapter,
        "mem0": mem0_adapter,
        "supermemory": supermemory_adapter,
        "letta": letta_adapter,
    }
    return dispatch[request.param]


@pytest.fixture(autouse=True)
def _clean_db(request):
    """Truncate the memories table between every test that uses the adapter."""
    needs_db = any(
        name in request.fixturenames
        for name in (
            "adapter",
            "postgres_adapter",
            "passthrough_adapter",
            "mem0_adapter",
            "supermemory_adapter",
            "letta_adapter",
            "pg_url",
        )
    )
    if not needs_db:
        yield
        return
    pa = request.getfixturevalue("postgres_adapter")
    with pa._pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute("TRUNCATE TABLE memories")
        conn.commit()
    yield


# ---------------------------------------------------------------------------
# US2 — in-process OMP HTTP shim built on httpx.MockTransport.
#
# Routes (method, path-template) → handlers that delegate to a real
# PostgresAdapter so passthrough contract tests round-trip real data.
# ---------------------------------------------------------------------------


def _build_omp_mock_server(backend) -> httpx.MockTransport:
    """Return an httpx.MockTransport that speaks the OMP HTTP contract.

    ``backend`` is a real adapter instance (PostgresAdapter) used for
    durable storage. The shim only marshals JSON ↔ pydantic models and
    maps verb-level exceptions to the OMP error envelope.
    """
    from openmem.errors import OMPError
    from openmem.types import (
        Capabilities,
        CapabilityFeatures,
        MemoryInput,
        MemoryUpdate,
    )

    _ID_RE = re.compile(r"^/memories/([^/]+)$")

    def _err_response(exc: OMPError) -> httpx.Response:
        status = {
            "not_found": 404,
            "invalid_request": 400,
            "unauthorized": 401,
            "rate_limited": 429,
            "unsupported_capability": 501,
            "scope_denied": 403,
            "provider_error": 500,
        }.get(exc.code, 500)
        return httpx.Response(status, json=exc.to_envelope())

    def _parse_dt(value: str | None) -> datetime | None:
        if value is None:
            return None
        return datetime.fromisoformat(value)

    def _json(model: Any) -> dict[str, Any]:
        return model.model_dump(mode="json", exclude_none=True)

    def handler(request: httpx.Request) -> httpx.Response:
        method = request.method
        path = request.url.path
        params = dict(request.url.params)
        body: dict[str, Any] = {}
        if request.content:
            import json as _json_mod
            try:
                body = _json_mod.loads(request.content)
            except Exception:
                body = {}

        try:
            # ----- capabilities probe ----------------------------------
            if method == "GET" and path == "/capabilities":
                caps = Capabilities(
                    omp_version="0.1",
                    provider="passthrough",
                    verbs=[
                        "add", "get", "update", "delete",
                        "list", "search", "context",
                    ],
                    features=CapabilityFeatures(
                        vector_search=True, scopes="native"
                    ),
                )
                return httpx.Response(200, json=_json(caps))

            # ----- /memories collection -------------------------------
            if method == "POST" and path == "/memories":
                mem = backend.add(MemoryInput.model_validate(body))
                return httpx.Response(200, json=_json(mem))
            if method == "GET" and path == "/memories":
                page = backend.list(
                    user_id=params["user_id"],
                    scope=params.get("scope"),
                    tag=params.get("tag"),
                    since=_parse_dt(params.get("since")),
                    until=_parse_dt(params.get("until")),
                    limit=int(params.get("limit", 50)),
                    cursor=params.get("cursor"),
                )
                return httpx.Response(200, json=_json(page))

            # ----- /memories/{id} item --------------------------------
            m = _ID_RE.match(path)
            if m is not None:
                mid = m.group(1)
                if method == "GET":
                    return httpx.Response(200, json=_json(backend.get(mid)))
                if method == "PATCH":
                    upd = MemoryUpdate.model_validate(body)
                    return httpx.Response(
                        200, json=_json(backend.update(mid, upd))
                    )
                if method == "DELETE":
                    backend.delete(mid)
                    return httpx.Response(204)

            # ----- /memories/search ------------------------------------
            if method == "POST" and path == "/memories/search":
                results = backend.search(
                    query=body["query"],
                    user_id=body["user_id"],
                    scope=body.get("scope"),
                    limit=int(body.get("limit", 10)),
                    min_score=body.get("min_score"),
                )
                return httpx.Response(
                    200, json=[_json(r) for r in results]
                )

            # ----- /context -------------------------------------------
            if method == "POST" and path == "/context":
                ctx = backend.context(
                    query=body["query"],
                    user_id=body["user_id"],
                    scope=body.get("scope"),
                    token_budget=int(body.get("token_budget", 500)),
                )
                return httpx.Response(200, json=_json(ctx))

            return httpx.Response(404, text=f"no route: {method} {path}")
        except OMPError as exc:
            return _err_response(exc)

    return httpx.MockTransport(handler)


@pytest.fixture(scope="module")
def _omp_mock_server(postgres_adapter) -> httpx.MockTransport:
    """In-process OMP HTTP shim backed by the module's PostgresAdapter."""
    return _build_omp_mock_server(postgres_adapter)


@pytest.fixture(scope="module")
def passthrough_adapter(_omp_mock_server):
    """PassthroughAdapter wired to the in-process OMP shim."""
    from openmem.adapters.passthrough import PassthroughAdapter

    return PassthroughAdapter(
        base_url="http://omp.test",
        transport=_omp_mock_server,
    )


# ---------------------------------------------------------------------------
# US3 — translation adapters wired against PostgresAdapter via SDK shims.
#
# Selection logic per fixture:
#   - if env var present → live mode against the real provider
#   - else → mock mode: shim that delegates to the module's PostgresAdapter
# ---------------------------------------------------------------------------


class _Mem0ClientShim:
    """Minimal mem0ai-shaped client backed by a PostgresAdapter."""

    def __init__(self, backend) -> None:
        self._b = backend
        # page-int → opaque cursor map (page 1 has cursor None).
        self._cursors: dict[int, str | None] = {1: None}

    @staticmethod
    def _x_extras(meta: dict) -> dict:
        return {k: v for k, v in (meta or {}).items() if k.startswith("x-")}

    def add(self, *, messages, user_id, metadata=None):
        from openmem.types import MemoryInput, MemorySource

        meta = metadata or {}
        src = meta.get("source")
        kw: dict[str, Any] = {
            "content": messages[0]["content"],
            "user_id": user_id,
            "scope": meta.get("scope"),
            "tags": meta.get("tags"),
            "confidence": meta.get("confidence"),
            "supersedes": meta.get("supersedes"),
        }
        if isinstance(src, dict):
            kw["source"] = MemorySource(**src)
        kw = {k: v for k, v in kw.items() if v is not None}
        kw.update(self._x_extras(meta))
        mem = self._b.add(MemoryInput(**kw))
        out_meta = dict(meta)
        out_meta["scope"] = mem.scope
        out_meta["tags"] = mem.tags
        if mem.supersedes:
            out_meta["supersedes"] = mem.supersedes
        for k, v in (mem.model_extra or {}).items():
            if k.startswith("x-"):
                out_meta[k] = v
        return [
            {
                "id": mem.id,
                "memory": mem.content,
                "user_id": mem.user_id,
                "created_at": mem.created_at.isoformat(),
                "metadata": out_meta,
            }
        ]

    def get(self, *, memory_id):
        mem = self._b.get(memory_id)
        meta: dict[str, Any] = {
            "scope": mem.scope,
            "tags": mem.tags,
            "source": (
                mem.source.model_dump(exclude_none=True) if mem.source else None
            ),
        }
        if mem.supersedes:
            meta["supersedes"] = mem.supersedes
        for k, v in (mem.model_extra or {}).items():
            if k.startswith("x-"):
                meta[k] = v
        return {
            "id": mem.id,
            "memory": mem.content,
            "user_id": mem.user_id,
            "created_at": mem.created_at.isoformat(),
            "updated_at": mem.updated_at.isoformat() if mem.updated_at else None,
            "metadata": meta,
        }

    def update(self, *, memory_id, data):
        from openmem.types import MemoryUpdate

        return self._b.update(memory_id, MemoryUpdate(content=data))

    def update_metadata(self, *, memory_id, metadata):
        from openmem.types import MemoryUpdate

        upd = MemoryUpdate(
            scope=metadata.get("scope"),
            tags=metadata.get("tags"),
            confidence=metadata.get("confidence"),
            supersedes=metadata.get("supersedes"),
        )
        return self._b.update(memory_id, upd)

    def delete(self, *, memory_id):
        self._b.delete(memory_id)

    def get_all(self, *, user_id, limit=50, page=1):
        cursor = self._cursors.get(page)
        page_obj = self._b.list(user_id=user_id, limit=limit, cursor=cursor)
        # Remember the next cursor so a subsequent page=N+1 call works.
        self._cursors[page + 1] = page_obj.next_cursor
        return [
            {
                "id": m.id,
                "memory": m.content,
                "user_id": m.user_id,
                "created_at": m.created_at.isoformat(),
                "metadata": {"scope": m.scope, "tags": m.tags},
            }
            for m in page_obj.items
        ]

    def search(self, *, query, user_id, limit=10):
        results = self._b.search(query=query, user_id=user_id, limit=limit)
        return [
            {
                "id": r.memory.id,
                "memory": r.memory.content,
                "user_id": r.memory.user_id,
                "created_at": r.memory.created_at.isoformat(),
                "metadata": {"scope": r.memory.scope, "tags": r.memory.tags},
                "score": r.score,
            }
            for r in results
        ]


@pytest.fixture(scope="module")
def mem0_adapter(postgres_adapter):
    """Mem0Adapter wired to a PostgresAdapter-backed shim (mock mode)."""
    if os.environ.get("MEM0_API_KEY"):  # pragma: no cover - live mode
        from openmem.adapters.mem0 import Mem0Adapter

        return Mem0Adapter(api_key=os.environ["MEM0_API_KEY"])
    from openmem.adapters.mem0 import Mem0Adapter

    return Mem0Adapter(
        api_key="sk-mock", client=_Mem0ClientShim(postgres_adapter)
    )


def _build_supermemory_transport(backend) -> httpx.MockTransport:
    """REST shim mimicking Supermemory backed by PostgresAdapter."""
    import json as _json_mod
    import re as _re

    from openmem.errors import OMPError
    from openmem.types import MemoryInput, MemorySource

    _ID_RE = _re.compile(r"^/memories/([^/]+)$")

    def _err_response(exc: OMPError) -> httpx.Response:
        status = {
            "not_found": 404,
            "invalid_request": 400,
            "unauthorized": 401,
            "rate_limited": 429,
            "scope_denied": 403,
        }.get(exc.code, 500)
        return httpx.Response(status, text=str(exc))

    def _to_record(mem) -> dict[str, Any]:
        meta: dict[str, Any] = {
            "scope": mem.scope,
            "tags": mem.tags,
            "source": (
                mem.source.model_dump(exclude_none=True) if mem.source else None
            ),
        }
        for k, v in (mem.model_extra or {}).items():
            if k.startswith("x-"):
                meta[k] = v
        return {
            "id": mem.id,
            "content": mem.content,
            "user_id": mem.user_id,
            "created_at": mem.created_at.isoformat(),
            "metadata": meta,
        }

    def handler(request: httpx.Request) -> httpx.Response:
        method = request.method
        path = request.url.path
        params = dict(request.url.params)
        body: dict[str, Any] = {}
        if request.content:
            try:
                body = _json_mod.loads(request.content)
            except Exception:
                body = {}
        try:
            if method == "POST" and path == "/memories":
                meta = body.get("metadata") or {}
                src = meta.get("source")
                kw: dict[str, Any] = {
                    "content": body["content"],
                    "user_id": body["user_id"],
                    "scope": meta.get("scope"),
                    "tags": meta.get("tags"),
                }
                if isinstance(src, dict):
                    kw["source"] = MemorySource(**src)
                # Pass-through x-* extension keys via MemoryInput extras.
                for k, v in meta.items():
                    if k.startswith("x-"):
                        kw[k] = v
                mem = backend.add(
                    MemoryInput(**{k: v for k, v in kw.items() if v is not None})
                )
                return httpx.Response(200, json=_to_record(mem))
            if method == "GET" and path == "/memories":
                page = backend.list(
                    user_id=params["user_id"],
                    limit=int(params.get("limit", 50)),
                    cursor=params.get("cursor"),
                )
                return httpx.Response(
                    200,
                    json={
                        "items": [_to_record(m) for m in page.items],
                        "next_cursor": page.next_cursor,
                    },
                )
            m = _ID_RE.match(path)
            if m is not None:
                mid = m.group(1)
                if method == "GET":
                    return httpx.Response(200, json=_to_record(backend.get(mid)))
                if method == "DELETE":
                    backend.delete(mid)
                    return httpx.Response(204)
            if method == "POST" and path == "/memories/search":
                results = backend.search(
                    query=body["query"],
                    user_id=body["user_id"],
                    limit=int(body.get("limit", 10)),
                    min_score=body.get("threshold"),
                )
                return httpx.Response(
                    200,
                    json=[
                        {**_to_record(r.memory), "score": r.score}
                        for r in results
                    ],
                )
            return httpx.Response(404, text=f"no route: {method} {path}")
        except OMPError as exc:
            return _err_response(exc)

    return httpx.MockTransport(handler)


@pytest.fixture(scope="module")
def supermemory_adapter(postgres_adapter):
    """SupermemoryAdapter wired to a PostgresAdapter-backed REST shim."""
    if os.environ.get("SUPERMEMORY_API_KEY"):  # pragma: no cover
        from openmem.adapters.supermemory import SupermemoryAdapter

        return SupermemoryAdapter(api_key=os.environ["SUPERMEMORY_API_KEY"])
    from openmem.adapters.supermemory import SupermemoryAdapter

    return SupermemoryAdapter(
        api_key="sk-mock",
        base_url="http://supermemory.test",
        transport=_build_supermemory_transport(postgres_adapter),
    )


class _LettaPassagesShim:
    """letta-client agents.passages shim backed by PostgresAdapter."""

    def __init__(self, backend, parent) -> None:
        self._b = backend
        self._parent = parent

    def _user_for(self, agent_id: str) -> str:
        return self._parent._reverse_agents.get(agent_id, "")

    @staticmethod
    def _strip(pg_id: str) -> str:
        return pg_id[4:] if pg_id.startswith("mem_") else pg_id

    @staticmethod
    def _restore(passage_id: str) -> str:
        return passage_id if passage_id.startswith("mem_") else f"mem_{passage_id}"

    def create(self, *, agent_id, text, metadata=None):
        from openmem.types import MemoryInput

        meta = metadata or {}
        kw: dict[str, Any] = {
            "content": text,
            "user_id": self._user_for(agent_id),
            "scope": meta.get("scope"),
            "tags": meta.get("tags"),
        }
        kw = {k: v for k, v in kw.items() if v is not None}
        for k, v in meta.items():
            if k.startswith("x-"):
                kw[k] = v
        mem = self._b.add(MemoryInput(**kw))
        out_meta = dict(meta)
        out_meta["scope"] = mem.scope
        out_meta["tags"] = mem.tags
        for k, v in (mem.model_extra or {}).items():
            if k.startswith("x-"):
                out_meta[k] = v
        return {
            "id": self._strip(mem.id),
            "text": mem.content,
            "created_at": mem.created_at.isoformat(),
            "metadata": out_meta,
        }

    def retrieve(self, *, agent_id, passage_id):
        mem = self._b.get(self._restore(passage_id))
        meta: dict[str, Any] = {"scope": mem.scope, "tags": mem.tags}
        for k, v in (mem.model_extra or {}).items():
            if k.startswith("x-"):
                meta[k] = v
        return {
            "id": self._strip(mem.id),
            "text": mem.content,
            "created_at": mem.created_at.isoformat(),
            "metadata": meta,
        }

    def delete(self, *, agent_id, passage_id):
        self._b.delete(self._restore(passage_id))

    def list(self, *, agent_id, limit=50, after=None):
        user_id = self._user_for(agent_id)
        # `after` arrives as the adapter-encoded id ("mem_<agent>_<passage>").
        # Decode to the stripped passage-id we used as the map key.
        after_key = None
        if after:
            rest = after[4:] if after.startswith("mem_") else after
            sep = rest.rfind("_")
            after_key = rest[sep + 1 :] if sep >= 0 else rest
        pg_cursor = self._parent._after_to_cursor.get(after_key) if after_key else None
        page = self._b.list(user_id=user_id, limit=limit, cursor=pg_cursor)
        if page.items and page.next_cursor:
            last_id = self._strip(page.items[-1].id)
            self._parent._after_to_cursor[last_id] = page.next_cursor
        return [
            {
                "id": self._strip(m.id),
                "text": m.content,
                "created_at": m.created_at.isoformat(),
                "metadata": {"scope": m.scope, "tags": m.tags},
            }
            for m in page.items
        ]

    def search(self, *, agent_id, query, limit=10):
        user_id = self._user_for(agent_id)
        results = self._b.search(query=query, user_id=user_id, limit=limit)
        return [
            {
                "passage": {
                    "id": self._strip(r.memory.id),
                    "text": r.memory.content,
                    "created_at": r.memory.created_at.isoformat(),
                    "metadata": {"scope": r.memory.scope, "tags": r.memory.tags},
                },
                "score": r.score,
            }
            for r in results
        ]


class _LettaAgentsShim:
    def __init__(self, backend, parent) -> None:
        self._b = backend
        self._parent = parent
        self.passages = _LettaPassagesShim(backend, parent)

    def create(self, *, name):
        # Derive a stable id from the agent name. Letta names are
        # `omp_<user_id>` per the adapter's `_agent_for`.
        agent_id = f"agent_{name}"
        if name.startswith("omp_"):
            self._parent._reverse_agents[agent_id] = name[len("omp_"):]
        cls = type("Agent", (), {"id": agent_id})
        return cls()


class _LettaClientShim:
    """letta-client shim backed by PostgresAdapter."""

    def __init__(self, backend) -> None:
        self._reverse_agents: dict[str, str] = {}
        self._after_to_cursor: dict[str, str] = {}
        self.agents = _LettaAgentsShim(backend, self)


@pytest.fixture(scope="module")
def letta_adapter(postgres_adapter):
    """LettaAdapter wired to a PostgresAdapter-backed shim (mock mode)."""
    if os.environ.get("LETTA_API_KEY"):  # pragma: no cover
        from openmem.adapters.letta import LettaAdapter

        return LettaAdapter(api_key=os.environ["LETTA_API_KEY"])
    from openmem.adapters.letta import LettaAdapter

    return LettaAdapter(
        api_key="sk-mock", client=_LettaClientShim(postgres_adapter)
    )



# ---------------------------------------------------------------------------
# Capability-aware skip hook (Principle II / SC-005).
#
# Some translation adapters intentionally do not advertise certain verbs
# (e.g. Supermemory + Letta lack `update`). Rather than editing the
# shared contract files, we skip parametrize cases at runtime when the
# adapter under test does not advertise the verb the test exercises.
# ---------------------------------------------------------------------------


_TEST_TO_VERB = {
    "test_update_supersedes_appends_to_history": "update",
}

# Verbs each adapter advertises (mirrors each adapter's _CAPS). Used to
# skip contract tests that exercise verbs an adapter does not support
# without editing the shared contract files (SC-005).
_ADAPTER_VERBS = {
    "postgres": {"add", "get", "update", "delete", "list", "search", "context", "audit"},
    "passthrough": {"add", "get", "update", "delete", "list", "search", "context", "audit"},
    "mem0": {"add", "get", "update", "delete", "list", "search", "context"},
    "supermemory": {"add", "get", "delete", "list", "search", "context"},
    "letta": {"add", "get", "delete", "list", "search", "context"},
}


def pytest_runtest_setup(item):
    verb = None
    for prefix, v in _TEST_TO_VERB.items():
        if item.name.startswith(prefix):
            verb = v
            break
    if verb is None:
        return
    callspec = getattr(item, "callspec", None)
    if callspec is None:
        return
    adapter_name = callspec.params.get("adapter")
    if adapter_name is None:
        return
    if verb not in _ADAPTER_VERBS.get(adapter_name, set()):
        pytest.skip(f"{adapter_name} does not advertise verb '{verb}'")
