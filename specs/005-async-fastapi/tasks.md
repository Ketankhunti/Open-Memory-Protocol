---
description: "Tasks for M3.2 Async facade + FastAPI passthrough server"
---

# Tasks: M3.2 Async facade + FastAPI passthrough server

**Input**: Design documents from `specs/005-async-fastapi/`
**Prerequisites**: [plan.md](plan.md), [spec.md](spec.md), [research.md](research.md), [data-model.md](data-model.md), [contracts/async-memory.md](contracts/async-memory.md), [contracts/http-server.md](contracts/http-server.md), [quickstart.md](quickstart.md)

**Tests**: Contract tests are MANDATORY per the spec (FR-008 cancellation, FR-015 OpenAPI conformance) and Constitution Principle II. The contract test inventory is fixed in `contracts/async-memory.md` §7 and `contracts/http-server.md` §8.

**Organization**: Tasks are grouped by user story. Per `plan.md`, this work ships as **two PRs on the same branch**:

- **PR-A** = Phases 1–5 (Setup + Foundational + US1 + US3 + US4 verification)
- **PR-B** = Phase 6 (US2 — FastAPI server). Opens against PR-A's merge commit.
- **Polish** = Phase 7 (CHANGELOG, version bump, README — split per PR per the notes inside Phase 7).

## Format: `- [ ] T### [P?] [Story?] Description`

- **[P]** = parallelizable (different files, no incomplete dependencies).
- **[Story]** = required for user-story phases only (US1, US2, US3, US4). Setup / Foundational / Polish phases carry no story label.
- Every task names exact file paths.

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Project initialization and packaging changes shared by both PRs.

- [X] T001 Create `sdk-python/openmem/adapters/async_base.py` placeholder (just the `AsyncBaseAdapter` Protocol stub with `pass` bodies and module docstring) so subsequent imports resolve during early scaffolding
- [X] T002 Create empty package directories: `sdk-python/tests/async/__init__.py` and `sdk-python/tests/server/__init__.py`
- [X] T003 [P] Add `[project.optional-dependencies]` block to `sdk-python/pyproject.toml` with `async = ["asyncpg>=0.29", "httpx>=0.27"]` and `server = ["openmem[async]", "fastapi>=0.115", "uvicorn[standard]>=0.30"]` per research §R5
- [X] T004 [P] Add `pytest-asyncio>=0.24` to `[project.optional-dependencies].dev` (or `[tool.uv]` dev block; whichever exists today) in `sdk-python/pyproject.toml` and configure `asyncio_mode = "auto"` under `[tool.pytest.ini_options]` per research §R9
- [X] T005 [P] Update `.gitignore` at repo root to add `*.egg-info/` for the `[server]` extra build artifacts if not already present (verify only; add if missing)

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Shared protocols, fixtures, and the SQL factoring that BOTH the AsyncMemory and the server depend on. **No user-story work may begin until this phase completes.**

- [X] T006 Define the full `AsyncBaseAdapter` Protocol in `sdk-python/openmem/adapters/async_base.py` per data-model §2 (all 10 verbs as `async def` returning the same types as `BaseAdapter`; include `close()` and Sequence/Mapping imports)
- [X] T007 Extract postgres SQL strings (DDL, vector index, `add`/`get`/`search`/`list`/`update`/`delete` queries) from `sdk-python/openmem/adapters/postgres.py` into a new module `sdk-python/openmem/adapters/_postgres_sql.py` and re-import them in the existing sync adapter — verify exit code 0 (sync test suite unchanged, no behavior change)
- [X] T008 [P] Add fact-recovery + user-id validation helpers to `sdk-python/openmem/adapters/_validation.py` (extract empty-`user_id` check that exists today inside each sync adapter so both sync and async share one implementation); update sync adapters to import from here
- [X] T009 [P] Create `sdk-python/tests/async/conftest.py` with: `pytest_asyncio.fixture` factories `_make_async_memory(provider, **kw)`, async finalizer that calls `await mem.close()`, and a `live_finalizer` that tracks created ids and `await mem.delete(id)` at teardown (mirrors the M2.1 sync pattern from `tests/conftest.py`)

**Checkpoint**: `AsyncBaseAdapter` exists; SQL is shared; async test fixtures ready. User-story phases may now proceed.

---

## Phase 3: User Story 1 — Async route handlers without blocking the loop (Priority: P1) 🎯 MVP

**Story Goal**: Developers can `await mem.add/search/...` from inside `async def` handlers (FastAPI / Starlette / aiohttp) without freezing the event loop. Concurrent fan-out via `asyncio.gather` becomes near-perfect parallelism on async-native adapters.

**Independent Test**: `tests/async/test_async_facade.py` plus `test_async_contract_lifecycle.py` parametrized over `postgres`, `passthrough`, `mem0`, `supermemory`, `letta` all pass. Manually verifiable per [quickstart.md](quickstart.md) §1–§3.

### Tests for User Story 1 (write FIRST, ensure they FAIL before implementation)

- [X] T010 [P] [US1] Write `sdk-python/tests/async/test_async_facade.py` covering signature parity (§1), construction lazy-I/O (§2 C-LIFE-1), close idempotency (C-LIFE-5), use-after-close error (C-LIFE-4), context-manager usage (C-LIFE-3), and **cross-loop misuse (C-LOOP-1, FR-010): construct under loop A, await under loop B, assert clear `RuntimeError` raised before any backend call** per `contracts/async-memory.md`
- [X] T011 [P] [US1] Write `sdk-python/tests/async/test_async_contract_lifecycle.py` parametrized over all 5 providers: `add → get → update → list → delete` round-trip with return-shape equality vs sync `Memory`; **also assert `asyncio.gather` of 100 concurrent `add()` calls against postgres completes in ≤2× single-add latency (SC-002)**
- [X] T012 [P] [US1] Write `sdk-python/tests/async/test_async_contract_search.py` parametrized over all 5 providers: `search` and `context` return-shape parity, user_id scoping enforcement
- [X] T013 [P] [US1] Write `sdk-python/tests/async/test_async_contract_errors.py` parametrized over all 5 providers: every error class fires under the same conditions as sync per `contracts/async-memory.md` §5
- [X] T014 [P] [US1] Write `sdk-python/tests/async/test_async_threadwrap.py`: executor isolation per instance, `executor.shutdown` on close, sync-state non-mutation, threadpool size override

### Implementation for User Story 1

- [X] T015 [P] [US1] Implement `sdk-python/openmem/adapters/async_postgres.py` — `AsyncPostgresAdapter` using `asyncpg.create_pool`, importing SQL from `_postgres_sql.py`; lazy pool init on first verb call; supports cancellation natively (research §R1)
- [X] T016 [P] [US1] Implement `sdk-python/openmem/adapters/async_passthrough.py` — `AsyncPassthroughAdapter` using `httpx.AsyncClient`; lazy client init; honors same timeout/retry/header conventions as sync `passthrough.py` (research §R2)
- [X] T017 [P] [US1] Implement `sdk-python/openmem/adapters/async_threadwrap.py` — `AsyncThreadwrapAdapter` constructor takes a sync `BaseAdapter` instance + `ThreadPoolExecutor`; every verb calls `loop.run_in_executor(executor, partial(sync_method, *args, **kw))`; `close()` calls `executor.shutdown(wait=False, cancel_futures=True)` (data-model §2 + contracts §6)
- [X] T018 [US1] Implement `sdk-python/openmem/async_memory.py` — `AsyncMemory` class with constructor (no I/O), provider routing (postgres/passthrough → native, mem0/supermemory/letta → threadwrap), `_loop_id` cross-loop guard, `_closed` state, `__aenter__`/`__aexit__`, `close()` idempotency (data-model §1 invariants AM-INV-1..7) — depends on T015, T016, T017
- [X] T019 [US1] Add lazy `__getattr__` to `sdk-python/openmem/__init__.py` per research §R6 — `from openmem import AsyncMemory` works iff `[async]` extra installed; raises `ImportError` with message containing exact string `pip install 'openmem[async]'` (FR-026, contracts §C-EXT-2); update `__all__` to include `"AsyncMemory"`
- [X] T020 [US1] Run the full sync test suite (`pytest sdk-python/tests -q --ignore=sdk-python/tests/async --ignore=sdk-python/tests/server`) and verify exit code 0 with the prior baseline (captured at branch-off from `main`) preserved — no tests removed, no new failures (FR-011 / SC-008 backstop) — depends on T018, T019
- [X] T021 [US1] Run the new async test suite (`pytest sdk-python/tests/async -q`) and verify all parametrized tests pass for all 5 providers; coverage gate ≥85% maintained — depends on T020

**Checkpoint**: `AsyncMemory` shipped, all 5 adapters async-callable, sync `Memory` byte-identical, contract tests green. **PR-A could merge here if US3 + US4 weren't bundled.**

---

## Phase 4: User Story 3 — Cancellation propagates and aborts in-flight work (Priority: P2)

**Story Goal**: Cancelling an `await mem.<verb>` releases the underlying postgres connection / HTTP socket within 500 ms (SC-003) and aborts the server-side query on postgres. Threadwrap adapters surface `CancelledError` immediately and document best-effort semantics.

**Independent Test**: `tests/async/test_async_cancellation.py::test_postgres_pool_release` and `::test_passthrough_socket_release` and `::test_threadwrap_immediate_cancel` all pass.

### Tests for User Story 3 (write FIRST)

- [X] T022 [P] [US3] Write `sdk-python/tests/async/test_async_cancellation.py::test_postgres_pool_release` per `contracts/async-memory.md` §3 C-CAN-2: spawn `mem.search(slow_query)`, cancel after 100ms, assert `pool._holders` count returns to baseline within 500ms (live-only, gated by `OMP_LIVE` + `OMP_POSTGRES_URL`)
- [X] T023 [US3] Add `::test_postgres_query_aborted` to the same file per C-CAN-3: assert `pg_stat_activity` shows no query matching the slow-sleep tag 1s after cancellation (live-only)
- [X] T024 [P] [US3] Add `::test_passthrough_socket_release` per C-CAN-2: cancel an in-flight `await mem.search(...)` against an httpx `MockTransport` whose response sleeps; assert the transport's "request started but not completed" counter increments and the awaiter sees `CancelledError` within 50ms
- [X] T025 [P] [US3] Add `::test_threadwrap_immediate_cancel` per C-CAN-4: use a controllable mock sync adapter whose `add()` blocks on a threading.Event; cancel the awaiter; assert `CancelledError` raised in ≤50ms while the worker thread keeps running; on subsequent event-set verify the orphan log line appears at DEBUG level
- [X] T026 [P] [US3] Add `::test_pool_state_after_cancel` per C-CAN-5: cancel a verb, then immediately await another verb on the same `AsyncMemory`; assert the second call succeeds (no pool corruption)

### Implementation for User Story 3

- [X] T027 [US3] Audit `sdk-python/openmem/adapters/async_postgres.py` (T015) to ensure connection acquire/release uses `async with self._pool.acquire() as conn:` form so cancellation auto-releases via `__aexit__`; add a short docstring referencing C-CAN-2/C-CAN-3
- [X] T028 [US3] Audit `sdk-python/openmem/adapters/async_passthrough.py` (T016) to confirm `httpx.AsyncClient` requests are issued without `timeout=None` and that no swallowing `try/except asyncio.CancelledError` exists; add docstring referencing C-CAN-2
- [X] T029 [US3] In `sdk-python/openmem/adapters/async_threadwrap.py` (T017), attach an `add_done_callback` (using the `asyncio.Future` returned by `loop.run_in_executor`) that logs at `logging.DEBUG` with message `"orphan call completed after cancellation: provider=%s verb=%s"` — implements C-CAN-4 visibility (best-effort, not a hard requirement)
- [X] T030 [US3] Run `pytest sdk-python/tests/async/test_async_cancellation.py -q` (with `OMP_LIVE=1` + `OMP_POSTGRES_URL` set) and verify all cancellation tests pass — depends on T027–T029

**Checkpoint**: Cancellation contract enforced and tested for all three tiers.

---

## Phase 5: User Story 4 — Sync `Memory` users keep working with no changes (Priority: P1)

**Story Goal**: Existing M1+M2+M2.1+M3.1 deployments upgrade to 0.4.0 with zero code changes. `Memory`'s public surface is byte-identical.

**Independent Test**: `pytest sdk-python/tests -q --ignore=sdk-python/tests/async --ignore=sdk-python/tests/server` reports exit code 0 with the baseline captured at branch-off (no tests removed, no new failures) without modification.

### Tests for User Story 4 (write FIRST)

- [X] T031 [P] [US4] Add `tests/async/test_async_facade.py::test_memory_signatures_unchanged` — store a JSON snapshot of `(name, kind, default, annotation_str)` tuples per parameter for `Memory.__init__` and every public method, committed at `tests/async/_signatures_baseline.json`; assert current introspection equals snapshot (per SC-008; JSON snapshot chosen over pickled `inspect.Signature` because the latter is not stable across Python micro versions)
- [X] T032 [P] [US4] Add `tests/async/test_packaging_extras.py::test_bare_install_imports_memory_only` — uses subprocess + `python -m venv` to create a clean venv with only the bare `openmem` install (no extras), then asserts `import openmem; from openmem import Memory` succeeds and `from openmem import AsyncMemory` raises `ImportError` whose message contains `pip install 'openmem[async]'` (FR-026, SC-007, C-EXT-1..3); skip with `pytest.skip` if the test environment cannot create a venv

### Implementation for User Story 4

- [X] T033 [US4] Run baseline regression: `pytest sdk-python/tests -q --ignore=sdk-python/tests/async --ignore=sdk-python/tests/server -p no:cacheprovider` and confirm exit code 0 with the prior baseline preserved (no tests removed, no new failures); if any sync test now fails, the change to `_postgres_sql.py` (T007) or `_validation.py` (T008) must be revisited

**Checkpoint**: PR-A end. All US1+US3+US4 tests green; existing tests unaffected. **Open PR-A → merge to `main` → checkout new branch off PR-A merge for PR-B.**

---

## Phase 6: User Story 2 — Run an OMP-compliant HTTP server in front of any provider (Priority: P1)

**Story Goal**: `omp-server --provider postgres --url ...` boots a FastAPI app that mirrors `spec/omp-0.1.openapi.yaml` 1:1. Every verb works for every provider with no provider-specific branches in the route code.

**Independent Test**: `tests/server/test_server_openapi_conformance.py` reports 100% schema-validation pass for all routes × representative success+error cases. `omp-server --help` runs and shows the trusted-network warning.

**Requires**: PR-A merged to `main` (this branch is rebased onto PR-A's merge commit).

### Tests for User Story 2 (write FIRST)

- [X] T034 [P] [US2] Write `sdk-python/tests/server/test_server_routes.py` — for each of the 9 routes in `contracts/http-server.md` §1, issue a happy-path request via `httpx.AsyncClient(app=app)` against a FastAPI app bound to the in-memory `passthrough` provider (no real backend); assert status code, response shape, and `Content-Type: application/json`; **also assert no `Access-Control-Allow-Origin` header is returned when `cors_origins` is empty (FR-022 default-deny)**
- [X] T035 [P] [US2] Write `sdk-python/tests/server/test_server_errors.py` parametrized over the 11-row error mapping table in `contracts/http-server.md` §3: trigger each exception via a mock adapter and assert status code + error envelope `code`
- [X] T036 [P] [US2] Write `sdk-python/tests/server/test_server_openapi_conformance.py` — load `spec/omp-0.1.openapi.yaml` once; for ≈25 success+error cases assert response body validates against the matching `responses[<status>].content["application/json"].schema`; assert `X-Request-Id` echoed
- [X] T037 [P] [US2] Write `sdk-python/tests/server/test_server_health.py` — `GET /healthz` returns 200 for postgres (mocked pool) and 503 when the pool acquire times out; for mem0/supermemory/letta returns 200 unconditionally per C-HEA-4
- [X] T038 [P] [US2] Write `sdk-python/tests/server/test_server_logging.py` — inject a request whose body contains the literal strings `"super_secret_password"` and `"u-alice-uid"`; capture log output via pytest's `caplog`; assert neither string appears (C-LOG-2 enforcement)
- [X] T039 [P] [US2] Write `sdk-python/tests/server/test_server_cli.py` — invoke `omp-server --help` via subprocess and assert stdout contains `"trusted-network deployment only"` and `"auth deferred"` (C-CLI-1); invoke `omp-server --version` and assert exit 0; invoke `omp-server --provider postgres` (no url, no env) and assert exit 2 with stderr starting `omp-server: missing config:` (C-CLI-3); **also `::test_boot_time` — `subprocess.Popen` `omp-server --provider passthrough --base-url http://127.0.0.1:9 --port <free>`; measure wall-clock from spawn to first `200 OK` on `/healthz`; assert ≤2 s and the immediately-following request ≤100 ms (SC-006)**
- [X] T040 [P] [US2] Write `sdk-python/tests/server/test_server_size_limit.py` — POST a body larger than `max_request_bytes` and assert `413` with `code = payload_too_large` (C-SIZ-1)
- [X] T040b [P] [US2] Write `sdk-python/tests/server/test_server_disconnect.py` — issue a slow `POST /v1/memories/search` via `httpx.AsyncClient(app=app)` against a mocked async adapter that sleeps; cancel the awaiter mid-flight; assert the underlying `AsyncMemory` pool/socket count returns to baseline within 1 s (FR-018, C-DIS-3)
- [X] T040c [P] [US2] Write `sdk-python/tests/server/test_throughput_bench.py::test_postgres_async_vs_sync` — live-only (gated by `OMP_LIVE=1` + `OMP_POSTGRES_URL`); spawn `omp-server --provider postgres` under uvicorn, drive `POST /v1/memories/search` for 30 s with 64 concurrent `httpx.AsyncClient` workers; compare against an equivalent sync-driver baseline (sync `Memory` + `ThreadPoolExecutor(64)`); assert async/sync RPS ratio ≥10× (SC-001); skip with clear message if `OMP_LIVE` unset

### Implementation for User Story 2

- [X] T041 [P] [US2] Implement `sdk-python/openmem/server/__init__.py` — re-export `app` (lazy via `__getattr__`, raising `ImportError` with `pip install 'openmem[server]'` if FastAPI missing — mirrors C-EXT-2) and `create_app(config)`
- [X] T042 [US2] Implement `sdk-python/openmem/server/app.py` — `create_app(config: OmpServerConfig) -> FastAPI` that builds the AsyncMemory, registers as `app.state.memory`, mounts routers from `routes.py`, registers exception handlers from `errors.py`, adds startup/shutdown hooks, configures logging middleware, optionally installs CORS per C-CORS-1/2 (data-model §4) — depends on T041
- [X] T043 [P] [US2] Implement `sdk-python/openmem/server/errors.py` — exception → HTTP status + `Error` envelope per the 11-row mapping in `contracts/http-server.md` §3 (FR-017); register as FastAPI `exception_handler`s
- [X] T044 [P] [US2] Implement `sdk-python/openmem/server/deps.py` — `async def get_memory(request) -> AsyncMemory` returning `request.app.state.memory`; helpers for `user_id` extraction from body or `X-User-Id` header with empty/whitespace rejection (C-UID-2)
- [X] T045 [US2] Implement `sdk-python/openmem/server/routes.py` — one `async def` handler per route in `contracts/http-server.md` §1; each calls the corresponding `AsyncMemory` verb; uses `Depends(get_memory)` and the `user_id` helper from T044 — depends on T043, T044
- [X] T046 [US2] Add `LoggingMiddleware` to `sdk-python/openmem/server/app.py` (or new `middleware.py` if cleaner) implementing C-LOG-1..3: emit one INFO line per request with method/path/status/latency/request_id; never touch body — depends on T042
- [X] T047 [US2] Add `MaxRequestSizeMiddleware` (or use Starlette's `BaseHTTPMiddleware`) per C-SIZ-1/C-SIZ-2: rejects with 413 before Pydantic parses
- [X] T048 [US2] Implement `sdk-python/openmem/server/cli.py` — `argparse`-based entry point per `contracts/http-server.md` §10 (CLI > env > default precedence); validates config (CFG-INV-1..4); boots uvicorn programmatically; prints `omp-server: serving <provider> at http://<host>:<port>` to stderr (C-CLI-4); on missing config exits 2 with stderr `omp-server: missing config: ...`
- [X] T049 [US2] Add console script entry to `sdk-python/pyproject.toml` under `[project.scripts]`: `omp-server = "openmem.server.cli:main"` (FR-024)
- [X] T050 [US2] Run the full server test suite (`pytest sdk-python/tests/server -q`) and verify all tests pass; coverage gate ≥85% maintained — depends on T034–T040c, T041–T049
- [ ] T051 [US2] Live smoke test: in a separate terminal, run `omp-server --provider postgres --url $env:OMP_POSTGRES_URL --port 8080` then execute the curl smoke test from [quickstart.md](quickstart.md) §2 and verify every response matches the expected output; cleanup any created memories

**Checkpoint**: PR-B end. `omp-server` runnable; OpenAPI conformance 100%; all 9 routes work for all 5 providers.

---

## Phase 7: Polish & Cross-Cutting Concerns

**Purpose**: Documentation, version bump, CHANGELOG. Tasks split per PR.

### Polish for PR-A (AsyncMemory)

- [X] T052 [P] Update root [README.md](README.md) Tooling section to mention `AsyncMemory` and link to [specs/005-async-fastapi/quickstart.md](specs/005-async-fastapi/quickstart.md) §1–§4
- [X] T053 [P] Update [sdk-python/README.md](sdk-python/README.md) with an "Async usage" subsection showing the `async with AsyncMemory(...)` pattern and the `[async]` install command
- [X] T054 Add `[0.4.0] — Unreleased (M3.2 PR-A — AsyncMemory)` section to [CHANGELOG.md](CHANGELOG.md) summarizing: new `AsyncMemory` facade, three-tier cancellation contract, `[async]` extra, threadpool wrapper for sync-only adapters, sync `Memory` byte-identical, new dep `asyncpg>=0.29`
- [X] T055 Bump `version` from `0.3.0` to `0.4.0` in `sdk-python/pyproject.toml` and update `__version__` in `sdk-python/openmem/__init__.py` if defined there
- [X] T056 Run `python -m pytest sdk-python/tests -q` (full suite) and confirm ≥85% coverage gate still passes — depends on T020, T021, T030, T033

### Polish for PR-B (FastAPI server)

- [X] T057 [P] Update root [README.md](README.md) with a new "HTTP server" section showing `pip install 'openmem[server]'` and the `omp-server --provider postgres` boot command
- [X] T058 [P] Update [sdk-python/README.md](sdk-python/README.md) Tooling section with an `omp-server` bullet linking to the quickstart
- [X] T059 Add `[0.5.0] — Unreleased (M3.2 PR-B — FastAPI server)` section to [CHANGELOG.md](CHANGELOG.md) summarizing: new `omp-server` console script, `[server]` extra, OpenAPI conformance test suite, `LoggingMiddleware` with sensitive-field redaction, `MaxRequestSizeMiddleware` (1 MiB default), opt-in CORS, security note that auth is deferred
- [X] T060 Bump `version` from `0.4.0` to `0.5.0` in `sdk-python/pyproject.toml`
- [X] T061 Run `omp-validate-spec ../omp-0.1.openapi.yaml` from `sdk-python/` to confirm the OpenAPI spec is still valid (sanity check — we did not modify it)
- [X] T062 Update [.github/copilot-instructions.md](.github/copilot-instructions.md) to repoint at the next milestone's plan once one is selected (deferred — leave pointing at this plan for now)

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)** → no dependencies
- **Foundational (Phase 2)** → depends on Setup; **BLOCKS all user stories**
- **US1 (Phase 3)** → depends on Foundational
- **US3 (Phase 4)** → depends on US1 (audits T015–T017 from US1; cancellation tests need a working `AsyncMemory`)
- **US4 (Phase 5)** → depends on US1 (T020 already runs sync tests in US1; T031–T033 add formal compat assertions)
- **US2 (Phase 6)** → depends on US1 + US3 + US4 being merged (PR-A); the server CANNOT exist without `AsyncMemory`
- **Polish (Phase 7)** → split: T052–T056 land in PR-A; T057–T062 land in PR-B

### PR Boundaries

- **PR-A**: Phases 1, 2, 3, 4, 5, 7-PR-A — opens against `main`, merges with all sync tests + new async tests green
- **PR-B**: Phase 6, 7-PR-B — opens against PR-A's merge commit on `main`, merges with all server tests + OpenAPI conformance green

### Within Each User Story

- Tests (T010–T014, T022–T026, T031–T032, T034–T040, T040b, T040c) are written FIRST and MUST FAIL before implementation
- For US1: adapters (T015–T017) before facade (T018) before lazy import (T019); regression check (T020) and async suite run (T021) gate the story
- For US2: dependency-injection helpers (T044) and exception handlers (T043) before routes (T045); middleware (T046, T047) and CLI (T048) parallel to routes after `app.py` (T042)

### Parallel Opportunities

- All Setup tasks T003–T005 marked [P] can run in parallel
- T008, T009 in Foundational marked [P] (different files, no inter-deps)
- US1 tests T010–T014 all parallel (different test files)
- US1 adapter implementations T015, T016, T017 all parallel (different files)
- US3 tests T022, T024, T025, T026 parallel; T023 sequential (extends T022's file)
- US2 tests T034–T040, T040b, T040c all parallel
- US2 implementations T041, T043, T044 parallel; T042/T045/T046/T047/T048 form a dependency chain
- Polish docs T052/T053 parallel within PR-A; T057/T058 parallel within PR-B

---

## Parallel Example: User Story 1 — write all contract tests in one go

```text
T010  [P] [US1] tests/async/test_async_facade.py
T011  [P] [US1] tests/async/test_async_contract_lifecycle.py
T012  [P] [US1] tests/async/test_async_contract_search.py
T013  [P] [US1] tests/async/test_async_contract_errors.py
T014  [P] [US1] tests/async/test_async_threadwrap.py
```

Then implement all three async adapters in parallel:

```text
T015  [P] [US1] adapters/async_postgres.py
T016  [P] [US1] adapters/async_passthrough.py
T017  [P] [US1] adapters/async_threadwrap.py
```

---

## Implementation Strategy

### MVP scope (for PR-A)

1. Phases 1–2 (Setup + Foundational) — unblocks everything
2. Phase 3 (US1) — `AsyncMemory` works for all 5 providers; sync tests still green
3. Phases 4–5 (US3 + US4) — cancellation contract enforced; compat formally asserted
4. Phase 7-PR-A (T052–T056) — docs + version bump
5. **STOP, open PR-A, merge.** This is a complete, valuable, releasable increment.

### Incremental delivery (PR-B)

6. Rebase a fresh branch off PR-A's merge commit
7. Phase 6 (US2) — FastAPI server
8. Phase 7-PR-B (T057–T061) — docs + version bump
9. **STOP, open PR-B, merge.** Server now consumes the `AsyncMemory` from PR-A.

### Per-task discipline

- Commit after each task or logical group
- Run `pytest -q` for the affected test directory after each implementation task
- The coverage gate (≥85%) is checked as part of CI on every push; do not let it drift

---

## Notes

- `[P]` = different files, no incomplete dependencies
- `[Story]` labels (US1/US2/US3/US4) only on Phase 3–6 tasks
- Setup, Foundational, and Polish phases carry no story label
- Every task names exact file paths so an LLM (or new contributor) can act on it without further context
- US4 (sync compat) is mostly **verification** rather than build — its only build is the snapshot/extras tests; the rest is the existing test suite continuing to pass
- The `omp-validate-spec` check (T061) is a smoke test only — we do NOT modify the OpenAPI spec in this milestone
- Live tests (T022, T023, T030, T040c, T051) require `OMP_LIVE=1` + `OMP_POSTGRES_URL` and are skipped otherwise

## Extension Hooks

**Optional Hook**: git
Command: `/speckit.git.commit`
Description: Auto-commit after task generation

Prompt: Commit task changes?
To execute: `/speckit.git.commit`
