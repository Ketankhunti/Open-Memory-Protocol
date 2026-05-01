# Feature Specification: M3.2 Async facade + FastAPI passthrough server

**Feature Branch**: `005-async-fastapi`
**Created**: 2026-05-01
**Status**: Draft
**Input**: User description: "AsyncMemory facade + FastAPI passthrough server. Keep `Memory` as canonical sync surface and add `AsyncMemory` alongside. Ship postgres + passthrough adapters as truly async first; remaining adapters (mem0/supermemory/letta) wrapped via threadpool. Combined with FastAPI passthrough server because the server is the primary consumer. Cancellation MUST propagate end-to-end and abort in-flight backend operations where supported."

## User Scenarios & Testing *(mandatory)*

### User Story 1 — FastAPI route handler reads/writes memory without blocking the event loop (Priority: P1)

A developer building an async web service (FastAPI / Starlette / aiohttp) wants to add OMP memory to a route handler without freezing the event loop on every call. They import `AsyncMemory`, construct it once at app startup, and `await mem.add(...)` / `await mem.search(...)` from any `async def` handler. Throughput stays bounded by network/db latency, not by Python thread contention.

**Why this priority**: Async web frameworks are the dominant deployment shape for new Python services in 2026. Without an async facade, OMP cannot be adopted by FastAPI/Starlette users without bespoke `run_in_threadpool` wrappers that defeat the framework's concurrency model. This is the single biggest blocker to wider Python adoption.

**Independent Test**: A FastAPI app with one `async def` route that calls `await mem.search(...)` against the postgres adapter sustains ≥500 RPS on a laptop-class machine while a sync-equivalent app sustains <50 RPS under the same load (10× improvement). Validated by an integration test with a benchmark assertion.

**Acceptance Scenarios**:

1. **Given** an `AsyncMemory` instance bound to the postgres adapter, **When** a caller invokes `await mem.add(content="x", user_id="alice")` from inside an `async def` function, **Then** the call completes successfully without blocking other coroutines on the same event loop.
2. **Given** the same instance, **When** the caller invokes `await asyncio.gather(*[mem.add(content=f, user_id="alice") for f in 100_facts])`, **Then** all 100 inserts complete in approximately the latency of a single insert plus pool overhead (≪ 100× sequential latency).
3. **Given** `AsyncMemory` bound to a sync-only backend (mem0/supermemory/letta), **When** a caller awaits a verb, **Then** the call still resolves successfully but is internally executed on a worker thread so the event loop is never blocked.

---

### User Story 2 — Run an OMP-compliant HTTP server in front of any provider (Priority: P1)

An operator wants to expose the same OMP verbs (`add`, `get`, `search`, `list`, `update`, `delete`) over HTTP so clients in other languages (or remote agents, or browser extensions) can use OMP without depending on the Python SDK. They run `omp-server --provider postgres --url ...` (or pass provider config via env) and the server boots a FastAPI app that mirrors `spec/omp-0.1.openapi.yaml` 1:1.

**Why this priority**: The OpenAPI spec has been the source of truth since M1 but no reference server consumes it. A passthrough server validates that the spec is implementable end-to-end, unlocks non-Python clients, and is a prerequisite for any future TypeScript/Go/Rust SDK that wants to integration-test against a real OMP endpoint.

**Independent Test**: Start `omp-server --provider postgres --url $OMP_POSTGRES_URL` on port 8080. From a separate process, run a curl-based smoke test that exercises every verb (add, get, search, list, update, delete) and asserts the responses validate against the OpenAPI schema. All status codes, error envelopes, and field names match the spec exactly.

**Acceptance Scenarios**:

1. **Given** `omp-server` running with the postgres provider, **When** a client `POST`s `{"content": "hi", "user_id": "alice"}` to `/v1/memories`, **Then** the response is `201 Created` with body matching the `Memory` schema in `omp-0.1.openapi.yaml`.
2. **Given** the same server, **When** a client requests a memory id that does not exist, **Then** the server returns `404` with an error envelope whose `code` is `not_found` (per the spec's `Error` schema).
3. **Given** the server, **When** a client sends a malformed `user_id` (empty string), **Then** the server returns `400` with `code = invalid_request` BEFORE the call reaches the underlying adapter.
4. **Given** the server is configured with any of the five providers, **When** the same OpenAPI-conformance test suite runs against it, **Then** all assertions pass with no provider-specific branches in the test code.

---

### User Story 3 — Cancellation propagates and aborts in-flight backend work (Priority: P2)

A developer's HTTP client disconnects (or the developer manually cancels an `asyncio.Task`) while a long-running `await mem.search(...)` is in flight. The `AsyncMemory` call raises `asyncio.CancelledError` at the await point, and on backends that support cancellation (postgres, passthrough HTTP) the underlying connection-level operation is actively aborted. The developer's app reclaims the connection slot immediately rather than waiting for the original call to drain.

**Why this priority**: Without cancellation propagation, a single slow query holds a db connection or HTTP socket for its full timeout even though no caller is waiting for the result. This breaks the resource-efficiency promise of async I/O and starves the connection pool under load. Marked P2 (not P1) because basic async functionality is more urgent than abort semantics, but cancellation is required for production-grade behavior.

**Independent Test**: Spawn an `await mem.search(...)` against a postgres adapter pointed at a slow query (artificial `pg_sleep(10)`). After 100 ms, cancel the awaiting task. Assert that (a) the cancellation raises `asyncio.CancelledError` within ≤50 ms, (b) the postgres connection is returned to the pool within ≤500 ms, and (c) the postgres server-side query is no longer running (verified via `pg_stat_activity`).

**Acceptance Scenarios**:

1. **Given** an in-flight `await mem.search(...)` on the postgres adapter, **When** the awaiting task is cancelled, **Then** the call raises `asyncio.CancelledError` and the underlying `asyncpg`/`psycopg` operation is cancelled at the driver level.
2. **Given** an in-flight `await mem.add(...)` on the passthrough adapter (HTTP), **When** the awaiting task is cancelled, **Then** the underlying `httpx.AsyncClient` request is aborted and the socket released.
3. **Given** an in-flight call on a threadpool-wrapped adapter (mem0/supermemory/letta), **When** the awaiting task is cancelled, **Then** the awaiter receives `CancelledError` immediately, and the worker thread completes its in-flight call in the background and discards the result (cancellation is best-effort for sync libraries; the contract is documented).

---

### User Story 4 — Sync `Memory` users keep working with no changes (Priority: P1)

An existing user of the synchronous `Memory` facade upgrades to the new release. They make zero code changes. Every script, notebook, and agent that currently imports `from openmem import Memory` continues to behave identically — same constructor signature, same return types, same error classes, same blocking call semantics.

**Why this priority**: Compatibility is non-negotiable. Any breakage to existing `Memory` users would invalidate every M1/M2/M3.1 deployment and erode trust. This story is P1 because failing it means the entire release must be rolled back regardless of how good `AsyncMemory` is.

**Independent Test**: Run the existing M1+M2+M3.1 test suite (currently 365 tests, 89% coverage) against the new release with no test modifications. Every passing test before the change must still pass after.

**Acceptance Scenarios**:

1. **Given** an existing script `from openmem import Memory; m = Memory(provider="postgres", url=...); m.add(...)`, **When** the script runs against the new release, **Then** behavior is byte-identical to the previous release for all observable outputs.
2. **Given** the existing eval kit (`openmem-eval`), **When** invoked with any provider, **Then** results are byte-identical to a run before the change (same recall, same MRR, same trace structure).

---

### Edge Cases

- **Event loop already running**: `AsyncMemory` constructor must NOT call `asyncio.run()` or any blocking I/O; it MUST be safe to instantiate inside an `async def`.
- **Reusing one `AsyncMemory` across loops**: Calling from a different event loop than the one alive at construction time MUST raise a clear error before any backend call.
- **`close()` not called**: Garbage-collecting an `AsyncMemory` without `await mem.close()` MUST NOT crash, but MUST log a warning if there are leaked connections/sockets.
- **Server backpressure**: If the FastAPI server's underlying connection pool is exhausted, the server MUST return `503 Service Unavailable` with `code = "provider_unavailable"` rather than queuing requests indefinitely.
- **Server auth**: Out of scope for v1 — the server runs unauthenticated on a private network only. A separate spec will add bearer-token auth.
- **Mixing `Memory` and `AsyncMemory` on the same backend**: Constructing both against the same postgres URL MUST work without exhausting the connection pool (each gets its own pool).
- **Cancellation during `add` that has already written to the backend**: If the row was committed before cancellation arrived, the cancellation MUST NOT roll it back — partial-write visibility matches the underlying backend's transaction semantics. The contract: cancellation is best-effort and observable side-effects may have occurred.
- **Threadpool starvation**: With sync-only adapters, more concurrent awaits than threadpool size MUST queue (not error). The default threadpool size MUST be configurable.

## Requirements *(mandatory)*

### Functional Requirements

#### AsyncMemory facade

- **FR-001**: System MUST expose a class `openmem.AsyncMemory` whose verb signatures mirror `openmem.Memory` exactly, with each verb declared `async def` and returning the same type as its sync counterpart.
- **FR-002**: `AsyncMemory.__init__` MUST accept the same `provider`, `url`, `api_key`, and adapter-specific kwargs as `Memory.__init__` and MUST NOT perform any blocking I/O.
- **FR-003**: `AsyncMemory` MUST expose `await mem.close()` to release all connections/sockets/clients held by the underlying adapter; calling `close()` more than once MUST be a no-op.
- **FR-004**: `AsyncMemory` MUST be usable as an async context manager (`async with AsyncMemory(...) as mem: ...`) which calls `close()` on exit.
- **FR-005**: For the `postgres` provider, `AsyncMemory` MUST use a real async PostgreSQL driver (no thread wrapping of sync `psycopg`).
- **FR-006**: For the `passthrough` provider, `AsyncMemory` MUST use a real async HTTP client (no thread wrapping of sync `httpx.Client`).
- **FR-007**: For the `mem0`, `supermemory`, and `letta` providers, `AsyncMemory` MUST wrap each sync verb call via a worker-thread executor so the event loop is never blocked. The threadpool size MUST be configurable per-`AsyncMemory` instance and MUST default to a value derived from `os.cpu_count()` (max 32).
- **FR-008**: `AsyncMemory` MUST emit `asyncio.CancelledError` from any awaited verb if the awaiting task is cancelled, and MUST attempt to abort the in-flight backend operation on backends that support driver-level cancellation (postgres async driver, async HTTP client). Cancellation on threadpool-wrapped backends MUST be best-effort: the awaiter sees `CancelledError` immediately while the worker thread completes its call in the background and discards the result.
- **FR-009**: `AsyncMemory` MUST raise the same error classes (`ProviderError`, `NotFoundError`, `InvalidRequestError`, `UnsupportedCapabilityError`, etc.) as `Memory` for the same conditions.
- **FR-010**: `AsyncMemory` MUST detect cross-loop misuse (constructed in loop A, awaited in loop B) and raise a clear error before any backend call.
- **FR-011**: System MUST keep the existing synchronous `openmem.Memory` class and all its current behavior unchanged. No constructor signature, return type, error class, or observable side-effect of `Memory` may change in this release.

#### FastAPI passthrough server

- **FR-012**: System MUST ship an executable `omp-server` console script that boots a FastAPI app exposing every verb defined in `spec/omp-0.1.openapi.yaml` at the paths declared by the spec.
- **FR-013**: `omp-server` MUST accept `--provider`, `--host`, `--port`, and provider-specific connection flags (e.g. `--url` for postgres) as CLI arguments and MUST also read the same values from the environment (`OMP_PROVIDER`, `OMP_POSTGRES_URL`, `MEM0_API_KEY`, `SUPERMEMORY_API_KEY`, `LETTA_API_KEY`).
- **FR-014**: The server MUST construct exactly one `AsyncMemory` instance at startup and reuse it for the lifetime of the process; per-request adapter construction is forbidden.
- **FR-015**: For every successful request, the response body MUST validate against the corresponding response schema in `spec/omp-0.1.openapi.yaml`.
- **FR-016**: For every error response, the body MUST be the spec's `Error` envelope (`{"error": {"code": "...", "message": "..."}}`) and the `code` MUST be one of the spec-enumerated codes (`not_found`, `invalid_request`, `provider_unavailable`, `unsupported_capability`, `ingestion_timeout`, etc.).
- **FR-017**: The server MUST translate adapter exceptions into HTTP status codes per a fixed mapping: `NotFoundError → 404`, `InvalidRequestError → 400`, `UnsupportedCapabilityError → 405`, `ProviderError(code="ingestion_timeout") → 504`, all other `ProviderError → 502`, unexpected `Exception → 500`.
- **FR-018**: The server MUST honor client disconnects: when the underlying ASGI scope reports `http.disconnect` for an in-flight request, the server MUST cancel the awaited adapter call (per FR-008 cancellation propagation).
- **FR-019**: The server MUST expose a `GET /healthz` endpoint that returns `200 OK` with `{"status": "ok"}` when the adapter is reachable and `503` with `{"error": {"code": "provider_unavailable", ...}}` otherwise. Health check MUST NOT exercise paid backend operations.
- **FR-020**: The server MUST log every request with method, path, status, latency, and a request id, but MUST NEVER log request bodies, response bodies, `user_id`, `api_key`, or any header named `Authorization`.
- **FR-021**: The server MUST reject request bodies larger than a configurable limit (default 1 MiB) with `413 Payload Too Large` BEFORE parsing, to defend against memory-exhaustion attacks.
- **FR-022**: The server MUST NOT enable CORS by default. CORS origins MUST be opt-in via `--cors-origins` / `OMP_CORS_ORIGINS` (comma-separated allowlist).
- **FR-023**: The server MUST NOT include any authentication mechanism in this release. The `omp-server --help` output and README MUST state explicitly that the server is intended for trusted-network deployment only and that auth will be added in a separate milestone.

#### Compatibility & packaging

- **FR-024**: `pyproject.toml` MUST declare `omp-server = "openmem.server.cli:main"` as a console script.
- **FR-025**: New dependencies (async postgres driver, async HTTP client, FastAPI, uvicorn) MUST be packaged as **extras**: `pip install openmem[async]` for `AsyncMemory`-only and `pip install openmem[server]` for the HTTP server. Bare `pip install openmem` MUST continue to install only the existing sync stack.
- **FR-026**: Importing `from openmem import AsyncMemory` MUST raise a clear `ImportError` with installation instructions if the `[async]` extra is not installed. Same for `from openmem.server import app` without `[server]`.

### Key Entities

- **`AsyncMemory`**: Async-native facade. Same verbs as `Memory`, returns same types, raises same errors. Owns the adapter-side resource pool (connections / sockets / threadpool).
- **`AsyncBaseAdapter`**: Internal protocol. Defines `async def add/get/search/list/update/delete/wait_for_ingest`. Each provider has either a native async implementation (postgres, passthrough) or a threadpool-wrapped sync adapter.
- **`OmpServer` (FastAPI app)**: HTTP server. Holds one `AsyncMemory`. Routes mirror the OpenAPI spec 1:1. Translates adapter exceptions into HTTP status + `Error` envelope.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A FastAPI service that calls `await mem.search(...)` against the postgres provider sustains **≥10× the throughput** (requests/second) of the equivalent sync service under identical load on the same hardware.
- **SC-002**: Running 100 concurrent `await mem.add(...)` calls against the postgres adapter completes in **under 2× the latency of a single sequential `add`** (i.e., near-perfect parallelism modulo pool size).
- **SC-003**: Cancelling an in-flight `await mem.search(...)` on the postgres or passthrough adapter releases the underlying connection/socket within **500 ms** of the cancellation.
- **SC-004**: Every existing (M1+M2+M3.1) test in the repository continues to pass without modification. Coverage gate ≥85% maintained.
- **SC-005**: An OpenAPI-conformance test suite running against `omp-server` (any provider) reports **100% of responses validate** against `spec/omp-0.1.openapi.yaml` for both success and error paths.
- **SC-006**: `omp-server` boots in **under 2 seconds** with the postgres provider on a laptop-class machine and serves the first request within **100 ms** of boot completion.
- **SC-007**: `pip install openmem` (no extras) succeeds and exposes only the existing sync surface; importing `AsyncMemory` raises `ImportError` with a clear remediation message.
- **SC-008**: A static line-count check confirms `Memory`'s public surface (constructor signature, method signatures, return types) is byte-identical to the previous release.

## Assumptions

- **Async postgres driver choice is implementation-detail**: The spec does not name `asyncpg` vs `psycopg[async]`. Either is acceptable provided FR-005 (real async, no threads) and FR-008 (cancellation) are met. The plan phase will pick one.
- **FastAPI is the chosen server framework**: The user's request explicitly named FastAPI. The plan may add Starlette as an underlying detail but the public footprint is FastAPI.
- **Server is unauthenticated in v1**: Trusted-network deployment only. Bearer-token / mTLS auth is a separate milestone.
- **No new OpenAPI fields**: The server consumes the existing `spec/omp-0.1.openapi.yaml` as-is. If the implementation discovers a gap in the spec, that gap is filed as a separate spec change, not bundled into this milestone.
- **Threadpool default sizing**: `min(32, (os.cpu_count() or 1) + 4)` (Python's stdlib default for `ThreadPoolExecutor`).
- **Python version**: ≥3.11 (matches existing `requires-python`). `asyncio.TaskGroup` and structured cancellation primitives are available.
- **No persistence layer in the server itself**: All state lives in the underlying provider. The server is stateless and horizontally scalable.
- **No streaming / SSE / WebSocket endpoints in v1**: The OpenAPI spec is request/response; the server matches that surface only.
- **Live tests gated by env**: Integration tests against real backends reuse the existing `OMP_LIVE=1` + per-provider `*_API_KEY` convention from M2.1.
