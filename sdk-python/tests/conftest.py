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


@pytest.fixture(params=["postgres", "passthrough"])
def adapter(request, postgres_adapter, passthrough_adapter):
    """Parametrized adapter fixture.

    To add a new adapter in M2:
      1. Add a fixture (e.g. ``mem0_adapter``) above.
      2. Append its name to ``params`` here and to the dispatch dict below.
    """
    dispatch = {
        "postgres": postgres_adapter,
        "passthrough": passthrough_adapter,
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
