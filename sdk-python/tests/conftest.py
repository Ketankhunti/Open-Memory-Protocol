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
from pathlib import Path
from typing import Any

import httpx
import pytest


def _load_dotenv() -> None:
    """Populate ``os.environ`` from a repo-root ``.env`` (no overwrite)."""
    here = Path(__file__).resolve()
    for parent in here.parents:
        env_file = parent / ".env"
        if env_file.is_file():
            for raw in env_file.read_text(encoding="utf-8").splitlines():
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                key = key.strip()
                value = value.strip().strip("'\"")
                if key and key not in os.environ:
                    os.environ[key] = value
            return


_load_dotenv()

# Keep test-time poll budgets short so async-ingestion adapters don't
# block the suite for 60 s when a delete-then-get races the poll loop.
# A real OMP_INGEST_TIMEOUT in the environment is honoured (no override).
# In live mode, raise the floor to 60 s — real providers (mem0, supermemory)
# need ~25-45 s for upstream ingestion to materialise.
if (os.environ.get("OMP_LIVE") or "").strip() == "1":
    os.environ.setdefault("OMP_INGEST_TIMEOUT", "45")
    # NOTE: We intentionally do NOT set OMP_INGEST_BLOCK globally here.
    # Mock-mode unit tests under tests/adapters/ exercise the raw
    # async-add response shape (`status='queued'`, etc.) and would break
    # if the adapter blocked on every add(). Live contract fixtures pass
    # `block_on_add=True` directly via the `mem0_adapter` /
    # `supermemory_adapter` factories below.
else:
    os.environ.setdefault("OMP_INGEST_TIMEOUT", "1")

# Use an env var so CI without docker can fall back to a real Postgres URL.
_USE_TESTCONTAINER = os.environ.get("PG_URL") is None


# ---------------------------------------------------------------------------
# M2.1 — strict live-mode env-var parsing (data-model.md §4a / FR-118).
#
# `OMP_LIVE` activates live mode iff stripped value is exactly "1".
# `<PROVIDER>_API_KEY` activates the matching provider iff stripped value
# is non-empty AFTER `.strip()`. Whitespace-only / malformed values keep
# the provider in mock mode (no half-configured states).
#
# API-key values MUST NEVER be logged — we only ever log a flag-state
# message, never the value, prefix, length, or hash (defends against
# credential exfiltration via debug logging).
# ---------------------------------------------------------------------------

import logging as _logging

_LIVE_LOG = _logging.getLogger("openmem.tests.live_mode")


def _is_live_mode_active(provider: str) -> bool:
    """Return True iff live mode is opt-in AND the matching key is set.

    Strict parsing per data-model.md §4a:
      - OMP_LIVE must equal exactly "1" after `.strip()`.
      - <PROVIDER>_API_KEY must be non-empty after `.strip()`.
    """
    if (os.environ.get("OMP_LIVE") or "").strip() != "1":
        return False
    key_name = f"{provider.upper()}_API_KEY"
    if not (os.environ.get(key_name) or "").strip():
        return False
    return True


# Maximum sleep we will honour from a server's `retry_after` hint, in
# seconds. Caps a hostile server's ability to stall the test suite via
# absurd retry_after values (defence in depth — EC-106).
_MAX_RETRY_AFTER = 30.0


def retry_once_on_rate_limit(fn, *, sleeper=None):
    """Invoke ``fn``; on `RateLimitedError`, sleep then re-invoke once.

    Honours the optional ``retry_after`` attribute (seconds) on the
    exception, capped at :data:`_MAX_RETRY_AFTER` to neutralise hostile
    or buggy servers. A second `RateLimitedError` propagates unchanged
    (EC-106 — we retry exactly once).
    """
    import time as _time

    from openmem.errors import RateLimitedError

    sleep = sleeper if sleeper is not None else _time.sleep
    try:
        return fn()
    except RateLimitedError as exc:
        retry_after_raw = getattr(exc, "retry_after", None)
        try:
            delay = float(retry_after_raw) if retry_after_raw is not None else 1.0
        except (TypeError, ValueError):
            delay = 1.0
        if delay < 0:
            delay = 1.0
        delay = min(delay, _MAX_RETRY_AFTER)
        sleep(delay)
        return fn()


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
    # M2.1 live-mode: between each test, delete only the ids that the
    # tracked finalizer has accumulated since the start of the run, so
    # leftover state from one test in the same module doesn't pollute
    # pagination/list assertions in the next. Touching only known ids
    # keeps overhead bounded (vs full list+delete-by-user_id sweeps).
    if (os.environ.get("OMP_LIVE") or "").strip() == "1":
        for fname, provider in (
            ("mem0_adapter", "mem0"),
            ("supermemory_adapter", "supermemory"),
            ("letta_adapter", "letta"),
        ):
            if fname not in request.fixturenames:
                continue
            if not _is_live_mode_active(provider):
                continue
            try:
                live_adapter = request.getfixturevalue(fname)
            except Exception:  # noqa: BLE001
                continue
            tracked = getattr(live_adapter, "_omp_test_created", None)
            if not tracked:
                continue
            # Snapshot + clear the tracker so the module-end finalizer
            # doesn't redundantly retry these ids.
            ids_to_clear = list(tracked)
            tracked.clear()
            for mid in ids_to_clear:
                try:
                    live_adapter.delete(mid)
                except Exception:  # noqa: BLE001
                    pass
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

    def update(self, *, memory_id, text=None, data=None, **_):
        from openmem.types import MemoryUpdate

        body = text if text is not None else data
        return self._b.update(memory_id, MemoryUpdate(content=body))

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

    def get_all(self, *, user_id=None, limit=50, page=1, page_size=None, version=None, filters=None, **_):
        # Mock shim accepts both legacy (user_id=) and v2 (filters=) shapes.
        if user_id is None and isinstance(filters, dict):
            user_id = filters.get("user_id")
            if user_id is None and "AND" in filters:
                for clause in filters.get("AND", []):
                    if clause.get("key") == "user_id":
                        user_id = clause.get("value")
                        break
        if page_size is not None:
            limit = page_size
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

    def search(self, *, query, user_id=None, limit=10, version=None, filters=None, **_):
        # Mock-mode shim for mem0 v2 search shape: real client expects
        # `filters={"user_id": "..."}` (since mem0ai 2.x). The shim accepts
        # either the legacy `user_id=` kwarg OR the new `filters={"user_id":...}`.
        if user_id is None and isinstance(filters, dict):
            user_id = filters.get("user_id") or filters.get("AND", [{}])[0].get("value")
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
def mem0_adapter(request, postgres_adapter):
    """Mem0Adapter: live mode iff OMP_LIVE=1 + MEM0_API_KEY; else mock."""
    if _is_live_mode_active("mem0"):  # pragma: no cover - live mode
        from openmem.adapters.mem0 import Mem0Adapter

        _LIVE_LOG.info("mem0 live mode enabled")
        adapter = Mem0Adapter(
            api_key=os.environ["MEM0_API_KEY"].strip(),
        )
        _register_live_finalizer(request, adapter, "mem0")
        return adapter
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

    _ID_RE = _re.compile(r"^/documents/([^/]+)$")

    def _extract_user_id(filters: dict | None) -> str:
        """Accept either legacy `{user_id:X}` or M2.1 `{AND:[{key,value}]}`."""
        if not isinstance(filters, dict):
            return ""
        if "user_id" in filters:
            return filters.get("user_id") or ""
        for clause in filters.get("AND") or []:
            if isinstance(clause, dict) and clause.get("key") == "user_id":
                return clause.get("value") or ""
        return ""

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
            "user_id": mem.user_id,  # M2.1 — user_id lives in metadata
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
            if method == "POST" and path == "/documents":
                meta = body.get("metadata") or {}
                src = meta.get("source")
                # M2.1: user_id is read from metadata.user_id (NOT top-level).
                user_id = meta.get("user_id") or body.get("user_id")
                kw: dict[str, Any] = {
                    "content": body["content"],
                    "user_id": user_id,
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
                # Mock-mode shim returns the full record (back-compat path
                # the adapter still recognises). The live API would return
                # only `{id, status:"queued"}` — we keep parity by adding a
                # `status` field to make the queued semantics observable.
                rec = _to_record(mem)
                rec["status"] = "queued"
                return httpx.Response(200, json=rec)
            # M2.1: list is now POST /documents/list with {limit, page, filters}.
            if method == "POST" and path == "/documents/list":
                filters = body.get("filters") or {}
                limit = int(body.get("limit", 50))
                page_num = int(body.get("page", 1))
                # Walk backend.list cursor-by-cursor up to the requested page
                # so the shim faithfully implements 1-indexed paging.
                user_id = _extract_user_id(filters)
                pg_cursor = None
                page_obj = backend.list(
                    user_id=user_id, limit=limit, cursor=pg_cursor
                )
                current = 1
                while current < page_num and page_obj.next_cursor is not None:
                    pg_cursor = page_obj.next_cursor
                    page_obj = backend.list(
                        user_id=user_id, limit=limit, cursor=pg_cursor
                    )
                    current += 1
                # Estimate totalPages: current page + 1 if more remain.
                total_pages = current + (1 if page_obj.next_cursor else 0)
                return httpx.Response(
                    200,
                    json={
                        "memories": [_to_record(m) for m in page_obj.items],
                        "pagination": {
                            "currentPage": current,
                            "limit": limit,
                            "totalPages": max(total_pages, current),
                        },
                    },
                )
            if method == "GET" and path == "/documents":
                # Legacy GET shape (kept for backward-compat).
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
            # M2.1: search is now POST /search with {q, limit, filters}.
            if method == "POST" and path == "/search":
                filters = body.get("filters") or {}
                results = backend.search(
                    query=body["q"],
                    user_id=_extract_user_id(filters),
                    limit=int(body.get("limit", 10)),
                    min_score=body.get("threshold"),
                )
                return httpx.Response(
                    200,
                    json={
                        "results": [
                            {
                                "documentId": r.memory.id,
                                "score": r.score,
                                "title": r.memory.content,
                                "chunks": [
                                    {"content": r.memory.content, "score": r.score}
                                ],
                                "metadata": {
                                    "user_id": r.memory.user_id,
                                    "scope": r.memory.scope,
                                    "tags": r.memory.tags,
                                },
                                "createdAt": r.memory.created_at.isoformat(),
                            }
                            for r in results
                        ],
                        "total": len(results),
                    },
                )
            if method == "POST" and path == "/memories/search":
                # Legacy search route (kept for backward-compat).
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
def supermemory_adapter(request, postgres_adapter):
    """SupermemoryAdapter: live mode iff OMP_LIVE=1 + SUPERMEMORY_API_KEY; else mock."""
    if _is_live_mode_active("supermemory"):  # pragma: no cover
        from openmem.adapters.supermemory import SupermemoryAdapter

        _LIVE_LOG.info("supermemory live mode enabled")
        adapter = SupermemoryAdapter(
            api_key=os.environ["SUPERMEMORY_API_KEY"].strip(),
        )
        _register_live_finalizer(request, adapter, "supermemory")
        return adapter
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

    def create(self, *, agent_id, text, metadata=None, tags=None, **_):
        from openmem.types import MemoryInput

        meta = metadata or {}
        # M2.1: live letta only persists `tags=[...]`. The adapter
        # encodes scope and `x-…` extension keys into reserved tag
        # prefixes (`_omp_scope:`, `_omp_x:<k>:<v>`). Mirror that
        # encoding here so the mock-mode shim round-trips identically.
        decoded_scope: str | None = meta.get("scope")
        decoded_tags: list[str] = list(meta.get("tags") or [])
        decoded_x: dict[str, Any] = {}
        for t in tags or []:
            ts = str(t)
            if ts.startswith("_omp_scope:"):
                decoded_scope = ts[len("_omp_scope:"):]
            elif ts.startswith("_omp_x:"):
                payload = ts[len("_omp_x:"):]
                k, _sep, v = payload.partition(":")
                if k:
                    decoded_x[k] = v
            else:
                decoded_tags.append(ts)
        kw: dict[str, Any] = {
            "content": text,
            "user_id": self._user_for(agent_id),
            "scope": decoded_scope,
            "tags": decoded_tags or None,
        }
        kw = {k: v for k, v in kw.items() if v is not None}
        for k, v in meta.items():
            if k.startswith("x-"):
                kw[k] = v
        for k, v in decoded_x.items():
            kw[k] = v
        mem = self._b.add(MemoryInput(**kw))
        out_tags: list[str] = list(mem.tags or [])
        if mem.scope is not None:
            out_tags.append(f"_omp_scope:{mem.scope}")
        for k, v in (mem.model_extra or {}).items():
            if k.startswith("x-"):
                out_tags.append(f"_omp_x:{k}:{v}")
        return {
            "id": self._strip(mem.id),
            "text": mem.content,
            "created_at": mem.created_at.isoformat(),
            "tags": out_tags,
        }

    def retrieve(self, *, agent_id, passage_id):
        mem = self._b.get(self._restore(passage_id))
        out_tags: list[str] = list(mem.tags or [])
        if mem.scope is not None:
            out_tags.append(f"_omp_scope:{mem.scope}")
        for k, v in (mem.model_extra or {}).items():
            if k.startswith("x-"):
                out_tags.append(f"_omp_x:{k}:{v}")
        return {
            "id": self._strip(mem.id),
            "text": mem.content,
            "created_at": mem.created_at.isoformat(),
            "tags": out_tags,
        }

    def delete(self, *, agent_id, passage_id):
        self._b.delete(self._restore(passage_id))

    def list(self, *, agent_id, limit=50, after=None):
        user_id = self._user_for(agent_id)
        # `after` arrives as the raw passage id (live letta cursor shape);
        # the adapter decodes its OMP cursor envelope before passing through.
        after_key = after if after else None
        pg_cursor = self._parent._after_to_cursor.get(after_key) if after_key else None
        page = self._b.list(user_id=user_id, limit=limit, cursor=pg_cursor)
        if page.items and page.next_cursor:
            last_id = self._strip(page.items[-1].id)
            self._parent._after_to_cursor[last_id] = page.next_cursor
        out: list[dict[str, Any]] = []
        for m in page.items:
            out_tags: list[str] = list(m.tags or [])
            if m.scope is not None:
                out_tags.append(f"_omp_scope:{m.scope}")
            for k, v in (m.model_extra or {}).items():
                if k.startswith("x-"):
                    out_tags.append(f"_omp_x:{k}:{v}")
            out.append({
                "id": self._strip(m.id),
                "text": m.content,
                "created_at": m.created_at.isoformat(),
                "tags": out_tags,
            })
        return out

    def search(self, *, agent_id, query, limit=10):
        user_id = self._user_for(agent_id)
        results = self._b.search(query=query, user_id=user_id, limit=limit)
        out: list[dict[str, Any]] = []
        for r in results:
            m = r.memory
            out_tags: list[str] = list(m.tags or [])
            if m.scope is not None:
                out_tags.append(f"_omp_scope:{m.scope}")
            for k, v in (m.model_extra or {}).items():
                if k.startswith("x-"):
                    out_tags.append(f"_omp_x:{k}:{v}")
            out.append({
                "passage": {
                    "id": self._strip(m.id),
                    "text": m.content,
                    "created_at": m.created_at.isoformat(),
                    "tags": out_tags,
                },
                "score": r.score,
            })
        return out


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
def letta_adapter(request, postgres_adapter):
    """LettaAdapter: live mode iff OMP_LIVE=1 + LETTA_API_KEY; else mock."""
    if _is_live_mode_active("letta"):  # pragma: no cover
        from openmem.adapters.letta import LettaAdapter

        _LIVE_LOG.info("letta live mode enabled")
        adapter = LettaAdapter(api_key=os.environ["LETTA_API_KEY"].strip())
        # M2.1: letta plans cap concurrent agents (default 3); proactively
        # purge any leftover agents from prior runs so this module's
        # fixtures don't fail with HTTP 402.
        try:
            for _agent in adapter._client.agents.list():
                try:
                    adapter._client.agents.delete(_agent.id)
                except Exception:  # noqa: BLE001
                    pass
        except Exception as exc:  # noqa: BLE001
            _LIVE_LOG.warning("letta pre-cleanup skipped: %s", type(exc).__name__)
        _register_live_finalizer(request, adapter, "letta")
        return adapter
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
    # M2.1 — letta drops `get` from its advertised verbs (FR-116). Skip
    # contract tests that exercise it for adapters that lack it.
    "test_add_then_get_roundtrip": "get",
    "test_delete_then_get_raises_not_found": "get",
    "test_status_round_trips": "get",
    "test_x_extension_field_round_trips_via_adapter": "get",
}

# Verbs each adapter advertises (mirrors each adapter's _CAPS). Used to
# skip contract tests that exercise verbs an adapter does not support
# without editing the shared contract files (SC-005).
_ADAPTER_VERBS = {
    "postgres": {"add", "get", "update", "delete", "list", "search", "context", "audit"},
    "passthrough": {"add", "get", "update", "delete", "list", "search", "context", "audit"},
    "mem0": {"add", "get", "update", "delete", "list", "search", "context"},
    "supermemory": {"add", "get", "delete", "list", "search", "context"},
    # M2.1: letta has no `get`, no `update`.
    "letta": {"add", "delete", "list", "search", "context"},
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


# ---------------------------------------------------------------------------
# M2.1 — live-mode test cleanup (FR-119 / EC-105 / data-model.md §4a).
#
# When a fixture switches to live mode it MUST register a finalizer that
# wipes the remote state it created. Failures are logged at WARNING and
# never raised — a flaky cleanup must not fail an unrelated test
# (EC-105). The wrapper below patches the adapter's `add` method to
# record returned ids on a per-fixture list; teardown iterates and
# deletes.
# ---------------------------------------------------------------------------


def _register_live_finalizer(request, adapter, provider: str) -> None:
    """Track ids returned by ``adapter.add`` and delete them at teardown."""
    created: list[str] = []
    user_ids: set[str] = set()
    # Expose state on the adapter for the per-test purge fixture below.
    adapter._omp_test_created = created
    adapter._omp_test_user_ids = user_ids
    original_add = adapter.add

    def wrapped_add(*args, **kwargs):
        memory = original_add(*args, **kwargs)
        try:
            mid = getattr(memory, "id", None)
            if mid:
                created.append(mid)
            uid = getattr(memory, "user_id", None)
            if uid:
                user_ids.add(uid)
        except Exception:  # noqa: BLE001
            pass
        return memory

    adapter.add = wrapped_add  # type: ignore[assignment]

    def cleanup():
        # Bulk path for mem0/supermemory: list-then-delete by user_id is
        # O(users) instead of O(memories) and avoids per-id resolution
        # latency that compounds across pagination tests (M2.1).
        bulk_done: set[str] = set()
        if provider in {"mem0", "supermemory"} and user_ids:
            for uid in user_ids:
                try:
                    page = adapter.list(uid, limit=100)
                    for m in page.items:
                        try:
                            adapter.delete(m.id)
                            bulk_done.add(m.id)
                        except Exception:  # noqa: BLE001
                            pass
                except Exception as exc:  # noqa: BLE001
                    _LIVE_LOG.warning(
                        "%s bulk cleanup failed for user_id=%s: %s",
                        provider,
                        uid,
                        type(exc).__name__,
                    )
        for mid in created:
            if mid in bulk_done:
                continue
            try:
                adapter.delete(mid)
            except Exception as exc:  # noqa: BLE001
                _LIVE_LOG.warning(
                    "%s live-mode cleanup failed for id=%s: %s",
                    provider,
                    mid,
                    type(exc).__name__,
                )
        # M2.1: letta accumulates one agent per distinct user_id; the
        # plan limit is small (3 by default). Delete any cached agents
        # so subsequent fixtures don't hit HTTP 402.
        agent_cache = getattr(adapter, "_agent_cache", None)
        client = getattr(adapter, "_client", None)
        if provider == "letta" and agent_cache and client is not None:
            for uid, agent_id in list(agent_cache.items()):
                try:
                    client.agents.delete(agent_id)
                except Exception as exc:  # noqa: BLE001
                    _LIVE_LOG.warning(
                        "letta live-mode agent cleanup failed for user_id=%s id=%s: %s",
                        uid,
                        agent_id,
                        type(exc).__name__,
                    )
            agent_cache.clear()

    request.addfinalizer(cleanup)


# ---------------------------------------------------------------------------
# M2.1 — live-marker collection hook (FR-121 / data-model.md §6).
#
# Tests carrying `@pytest.mark.live` are auto-skipped when the master
# `OMP_LIVE` switch is off. CI runs them only in a dedicated nightly job.
# ---------------------------------------------------------------------------


def pytest_collection_modifyitems(config, items):
    if (os.environ.get("OMP_LIVE") or "").strip() == "1":
        return
    skip_live = pytest.mark.skip(
        reason="live-mode test (set OMP_LIVE=1 to run; M2.1 / FR-121)"
    )
    for item in items:
        if item.get_closest_marker("live"):
            item.add_marker(skip_live)
