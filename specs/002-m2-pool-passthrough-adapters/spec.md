# Feature Specification: M2 — Connection pooling, native passthrough, first translation adapters

**Feature Branch**: `002-m2-pool-passthrough-adapters`
**Created**: 2026-04-28
**Status**: Draft
**Input**: User description: "M2: PostgresAdapter connection pool replacing RLock; PassthroughAdapter native verb forwarding via httpx; Mem0/Supermemory/Letta translation adapters passing the existing contract suite; pytest-timeout in dev extras; second real provider in 02_switch_providers.py example."

## Clarifications

### Session 2026-04-28

- Q: Which provider should `examples/02_switch_providers.py` (T032) pair with `postgres` to satisfy SC-008? → A: All three real providers (Mem0, Supermemory, Letta) using live API keys read from environment variables; the example skips any provider whose key is unset and prints a clear "set MEM0_API_KEY/SUPERMEMORY_API_KEY/LETTA_API_KEY to enable" hint instead of failing.

## User Scenarios & Testing *(mandatory)*

### User Story 1 — Postgres adapter scales under concurrent load (Priority: P1) 🎯 MVP

A high-traffic application uses `PostgresAdapter` from many request workers in parallel (web requests, background jobs, async task pools). With M1's global lock every verb serializes, capping throughput at one statement at a time and adding head-of-line blocking. In M2 the application sees real parallel DB I/O bounded only by the pool size, with no hangs and no driver-level deadlocks.

**Why this priority**: M1's `@_synchronized` lock is the documented stop-gap from [/memories/repo/m2-followups.md](/memories/repo/m2-followups.md). It guarantees correctness but blocks any production deployment that needs >1 concurrent request/second. Pooling unblocks the rest of the M2 story (translation adapters can then be written assuming concurrent calls are safe).

**Independent Test**: Run a load test that issues N concurrent `add()` calls against a pool of size K (e.g. N=200, K=10). Measured throughput is at least ⌈N/K⌉× faster than M1's serialized baseline; p99 latency is within 2× single-call latency; the existing `test_concurrent_inserts_do_not_deadlock` still passes; the contract suite is fully green.

**Acceptance Scenarios**:

1. **Given** a `PostgresAdapter` configured with `pool_size=10`, **When** 200 worker threads each call `mem.add()` concurrently, **Then** all 200 inserts succeed, the suite completes in less than the M1 baseline divided by 5, and no thread hangs.
2. **Given** a `PostgresAdapter`, **When** the application instantiates it without a `pool_size` argument, **Then** sensible defaults are used (min 1 connection, max equal to a documented value) and behavior is otherwise unchanged from M1.
3. **Given** a long-running adapter, **When** an underlying connection is broken (e.g. Postgres restart), **Then** the next verb call receives a fresh pooled connection or a single retry, and no permanently-poisoned connection remains in the pool.
4. **Given** code that calls `mem.update(id)` from inside a `mem.list()` iteration on the same adapter instance, **When** the call executes, **Then** it does not self-deadlock (the M1 `RLock` no longer guards verbs).

---

### User Story 2 — A native OMP service is a drop-in backend (Priority: P2)

An organization runs a memory service that already speaks OMP over HTTP (their own native implementation, or one of the adapters from US3). Today (M1) `PassthroughAdapter` only knows how to probe `/capabilities`; every other verb raises `NotImplementedError`. In M2 the application can point `Memory(provider="...", base_url="https://memory.example/")` at any OMP-conformant endpoint and use the full verb surface unchanged.

**Why this priority**: This is the protocol's core value proposition in action — *any* OMP-conformant service is callable through the SDK with no per-provider code. Until M2 only "translation" adapters work; M2 makes "passthrough" real and proves the wire spec round-trips.

**Independent Test**: Stand the `PostgresAdapter` (or any conformant fake) up behind a thin HTTP front that follows the OMP REST spec; from a separate process, instantiate `Memory(base_url=...)` and run the existing contract suite — every verb passes.

**Acceptance Scenarios**:

1. **Given** an OMP-conformant HTTP endpoint, **When** the user calls every verb (`add/get/update/delete/list/search/context/audit/capabilities`) via `Memory(base_url=...)`, **Then** each call serializes the request body per the OpenAPI spec and deserializes the response into the matching pydantic model.
2. **Given** the remote endpoint returns an OMP `Error` envelope, **When** any verb is called, **Then** the SDK raises the matching `OMPError` subclass (`NotFoundError`, `InvalidRequestError`, etc.) with `code`, `provider`, `request_id` populated.
3. **Given** the remote returns an HTTP 5xx with no OMP envelope, **When** the verb is called, **Then** the SDK raises `ProviderError` with the HTTP status code preserved.
4. **Given** the remote endpoint advertises `verbs` not including `audit`, **When** the user calls `mem.audit()`, **Then** the SDK raises `UnsupportedCapabilityError` *without* hitting the network (capability gate per FR-009).

---

### User Story 3 — Three real third-party providers swap in by name (Priority: P3)

A user already has data in Mem0 (or Supermemory or Letta). They install `openmem` and write the same code as the M1 quickstart, but pass `provider="mem0"` (or `"supermemory"` / `"letta"`). The SDK translates each OMP verb to that provider's native API. The existing contract suite proves the translation is conformant.

**Why this priority**: M2's third axis: *translation* adapters expand the matrix beyond Postgres. Each new adapter plugs into the contract suite by appending one fixture parameter (no test changes — Constitution Principle II).

**Independent Test**: For each of the three providers, set the relevant credentials via env vars, append the fixture to `tests/conftest.py`, run `pytest sdk-python/tests -q` — all contract tests pass for every adapter.

**Acceptance Scenarios**:

1. **Given** valid credentials for Mem0 (or Supermemory or Letta), **When** the user calls `Memory(provider="<name>", api_key="...")`, **Then** all OMP verbs the provider supports work end-to-end with no per-provider code in the user's app.
2. **Given** the provider does not support an OMP verb (e.g. no audit log), **When** the user calls that verb, **Then** the SDK raises `UnsupportedCapabilityError` and `mem.capabilities().verbs` does not list it (FR-009).
3. **Given** the provider's API rejects a request (rate limit, auth, bad input), **When** the verb is called, **Then** the SDK raises the matching `OMPError` subclass — never the underlying HTTP/SDK exception.
4. **Given** the parametrized contract suite with all four adapters (`postgres`, `mem0`, `supermemory`, `letta`), **When** `pytest sdk-python/tests -q` runs, **Then** every test passes for every adapter that meets its capability prerequisites.

---

### Edge Cases

- **EC-001 (pool exhaustion)**: When all pooled connections are checked out and a new verb call arrives, the call waits up to a documented timeout, then raises `ProviderError` with a clear "pool exhausted" message rather than hanging forever.
- **EC-002 (pool warm-up)**: The first call after `PostgresAdapter()` instantiation succeeds even before any connection has been used, by lazily opening on demand up to `min_size`.
- **EC-003 (passthrough capability mismatch)**: When the remote endpoint's `/capabilities` lists a verb but a call to that verb returns 501 Not Implemented, the SDK surfaces `UnsupportedCapabilityError` (not `ProviderError`) and the cached capabilities are not silently mutated.
- **EC-004 (passthrough redirect / non-2xx without envelope)**: 3xx redirects are followed once; 4xx without an OMP envelope becomes `InvalidRequestError`; 5xx without an envelope becomes `ProviderError`.
- **EC-005 (translation-adapter pagination differences)**: When the upstream provider uses page numbers instead of cursors, the adapter encodes the underlying state inside an opaque OMP `next_cursor` so the caller code remains unchanged.
- **EC-006 (translation-adapter scope semantics)**: When the upstream provider has no native `scope` concept, the adapter maps `scope` to a tag prefix (e.g. `__scope:coding/preferences`) and capabilities reports `scopes: "tags"`.
- **EC-007 (translation-adapter cross-model search)**: When the upstream provider auto-manages embeddings, the adapter omits `embedding_model` from inserts and disables the FR-014 cross-model hard-fail; capabilities reports the adapter's reality.
- **EC-008 (test hangs)**: When any test takes longer than the per-test timeout, pytest fails that test instantly with a clear stack trace; CI never hangs for >2× expected runtime.
- **EC-009 (RLock removed but called from old code)**: `PostgresAdapter._lock` no longer exists; any subclass or test that referenced the lock attribute receives an `AttributeError` at the boundary, with a CHANGELOG note documenting the removal.

## Requirements *(mandatory)*

### Functional Requirements

#### Connection pooling (US1)

- **FR-001**: `PostgresAdapter` MUST manage Postgres connections through a pool, not a single shared connection. Concurrent verb calls from multiple threads MUST be served by separate pooled connections rather than serialized behind a single lock.
- **FR-002**: `PostgresAdapter.__init__` MUST accept optional pool sizing arguments with documented defaults; behavior with no extra arguments MUST remain backward compatible with M1 callers.
- **FR-003**: `@_synchronized` and `self._lock` MUST be removed from `PostgresAdapter`. No verb body MUST hold a process-wide lock.
- **FR-004**: When the pool is exhausted, the adapter MUST wait up to a documented timeout, then raise `ProviderError`. It MUST NOT block forever.
- **FR-005**: Broken connections MUST NOT poison the pool. The adapter MUST recycle a bad connection on detection so the next verb call succeeds (or fails cleanly with a fresh attempt).

#### Native passthrough (US2)

- **FR-006**: `PassthroughAdapter` MUST implement every OMP verb (`add/get/update/delete/list/search/context/audit/capabilities`) by calling the corresponding HTTP endpoint defined in `spec/omp-0.1.openapi.yaml`.
- **FR-007**: `PassthroughAdapter` MUST serialize request bodies and parse response bodies using the existing pydantic models in `openmem.types` (no separate wire-only models).
- **FR-008**: When the remote returns an OMP `Error` envelope, `PassthroughAdapter` MUST raise the matching `OMPError` subclass via `OMPError.from_response_dict`.
- **FR-009**: Calls to verbs not advertised in the cached `capabilities().verbs` MUST raise `UnsupportedCapabilityError` *before* any network call (capability gate).
- **FR-010**: HTTP 4xx responses without an OMP envelope MUST become `InvalidRequestError`; 5xx without an envelope MUST become `ProviderError`.
- **FR-011**: `PassthroughAdapter` MUST send an `Authorization: Bearer <api_key>` header when an `api_key` is provided and MUST NOT log the key.

#### Translation adapters (US3)

- **FR-012**: The SDK MUST ship adapters for Mem0, Supermemory, and Letta, each subclassing `BaseAdapter` and exposing the same verb signatures.
- **FR-013**: Each translation adapter MUST pass the parametrized contract suite (`tests/test_contract_*.py`) for every verb it advertises in `capabilities().verbs`.
- **FR-014**: When an underlying provider exception is raised, each adapter MUST translate it to the matching `OMPError` subclass — callers MUST never see provider-specific exception types.
- **FR-015**: Adding any of the three new adapters to the test matrix MUST require zero changes to the test files — only one new entry in the `adapter` fixture's `params` and one new fixture function in `conftest.py`.
- **FR-016**: Each translation adapter's `capabilities()` MUST honestly report what the underlying provider can do (verbs, features, scope mode, e2e support).

#### Tooling & examples

- **FR-017**: `pytest-timeout` MUST be added to the `[dev]` extras and a default per-test timeout MUST be configured in `pyproject.toml` so a test cannot hang the suite for more than the configured limit.
- **FR-018**: `examples/02_switch_providers.py` MUST run the same `run(mem)` body against `postgres` and each of the three translation adapters (`mem0`, `supermemory`, `letta`) when their respective `*_API_KEY` env var is set. When a key is unset the provider MUST be skipped with a one-line hint (`set <KEY> to enable`) and the example MUST exit successfully as long as `postgres` plus at least one third-party provider ran.

### Key Entities

- **Connection pool**: Owned by `PostgresAdapter`; bounded set of psycopg connections that are checked out per verb call, returned on completion, recycled on error. Has size and timeout knobs.
- **OMP HTTP endpoint contract**: Mapping from verb → method/path/body/response defined in [spec/omp-0.1.openapi.yaml](spec/omp-0.1.openapi.yaml). `PassthroughAdapter` is its sole consumer in M2.
- **Translation adapter**: A `BaseAdapter` subclass that maps OMP verbs to a third-party SDK or HTTP API. Each one carries (a) credential config, (b) a fixed `Capabilities` payload, (c) input/output mappers, (d) error mappers.
- **Contract test fixture**: Single `adapter` fixture in `tests/conftest.py` parametrized over every adapter; the source of Constitution Principle II's guarantee that any green-suite adapter is a drop-in replacement.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Concurrent throughput on `PostgresAdapter.add` improves by at least 5× over M1 baseline at pool size 10 with 200 worker threads on the same hardware.
- **SC-002**: 0 lock-related test failures or hangs after the M1 `@_synchronized` decorator is removed; the existing concurrency test completes in under 30 seconds (M1: 5 minutes).
- **SC-003**: `Memory(base_url=<conformant_endpoint>)` passes 100% of the contract suite for every advertised verb.
- **SC-004**: Mem0, Supermemory, and Letta adapters each pass 100% of contract tests for verbs they advertise; mismatched verbs raise `UnsupportedCapabilityError` (no false greens, no false reds).
- **SC-005**: Adding a brand-new adapter to the conformance matrix requires changing exactly two files (`conftest.py` for the fixture entry and `memory.py` for the `_resolve_adapter` mapping) — zero edits in any `test_contract_*.py` file.
- **SC-006**: No test in the suite runs longer than the configured per-test timeout; the full suite completes in under 5 minutes on the standard CI runner.
- **SC-007**: 100% of provider exceptions raised inside a translation adapter are caught and translated; the integration test that fuzzes adapter inputs sees zero non-`OMPError` exceptions reach the caller.
- **SC-008**: `examples/02_switch_providers.py` runs the same `run(mem)` body against `postgres` and every translation adapter whose `*_API_KEY` env var is set, prints comparable outputs side-by-side, and exits 0 whenever `postgres` plus ≥1 third-party provider succeeded.

## Assumptions

- Postgres pooling will be implemented with `psycopg_pool.ConnectionPool` (already maintained by the psycopg authors). No bespoke pool implementation.
- Pool defaults: `min_size=1`, `max_size=10`, `timeout=30s`. These are documented in `sdk-python/README.md`.
- Mem0, Supermemory, and Letta each expose a public Python SDK or stable HTTP API; M2 targets the latest GA version of each as of 2026-04-28.
- Credentials for the three providers are out of scope for the SDK itself — tests use mocks/recorded fixtures by default and live integration is opt-in via env vars (`MEM0_API_KEY`, `SUPERMEMORY_API_KEY`, `LETTA_API_KEY`).
- Per-test timeout default: 30s. Tests that legitimately need more (e.g. concurrency stress) opt-in via `@pytest.mark.timeout(N)`.
- `PassthroughAdapter` in M2 is sync-only (matches the rest of the SDK). Async support is deferred.
- `02_switch_providers.py` runs `postgres` plus all three translation adapters (`mem0`, `supermemory`, `letta`) against the same `run(mem)` body using **live API keys** read from `MEM0_API_KEY` / `SUPERMEMORY_API_KEY` / `LETTA_API_KEY`. Providers whose keys are unset are skipped with a clear hint message; at least one third-party provider must be runnable for the example to satisfy SC-008.
