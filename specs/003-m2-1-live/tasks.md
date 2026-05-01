# Tasks: M2.1 — Live-API bridges for translation adapters

**Input**: Design documents from [specs/003-m2-1-live/](.)
**Prerequisites**: [plan.md](plan.md), [spec.md](spec.md), [research.md](research.md), [data-model.md](data-model.md), [contracts/](contracts/), [quickstart.md](quickstart.md)
**Tests**: REQUIRED — FR-120 mandates a new contract test; US4 is entirely test-infrastructure work; US1/US2/US3 acceptance criteria reference specific `pytest` invocations.

**Organization**: Tasks are grouped by user story per spec.md priority order:
US1 (mem0, P1, MVP) → US2 (supermemory, P2) → US3 (letta, P3) → US4 (live/mock coexistence, P2 — most of US4 lives in Foundational because every story needs it; the leftover validation lives in Phase 6).

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Maps to a user story in spec.md (US1, US2, US3, US4)

## Path Conventions

Single-project Python library — same layout as M1/M2:
- Sources: [sdk-python/openmem/](../../sdk-python/openmem/)
- Tests:   [sdk-python/tests/](../../sdk-python/tests/)
- Spec:    [spec/omp-0.1.openapi.yaml](../../spec/omp-0.1.openapi.yaml)
- Examples: [examples/](../../examples/)

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Dependency pins + pytest marker registration. No code changes yet.

- [X] T001 [P] Pin `mem0ai>=2.0,<3` and `letta-client>=1.10` in [sdk-python/pyproject.toml](../../sdk-python/pyproject.toml) `[project.optional-dependencies]` (`mem0`, `letta` extras)
- [X] T002 [P] Register `live` pytest marker in [sdk-python/pyproject.toml](../../sdk-python/pyproject.toml) under `[tool.pytest.ini_options].markers` (`live: tests that hit real provider APIs`)
- [X] T003 [P] Add `OMP_INGEST_TIMEOUT` to [.env.example](../../.env.example) (commented, default 60) and document `OMP_LIVE=1` semantics

**Checkpoint**: `pip install -e ".[mem0,letta,dev]"` resolves the new pins; `pytest --markers` lists `live`.

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Spec change, Pydantic mirror, error-code registration, shared bounded-poll helper, and the live/mock fixture machinery — all of which every user story depends on. Per Principle I, the OpenAPI schema change MUST land first.

**⚠️ CRITICAL**: No US1/US2/US3 work can begin until this phase is complete.

- [X] T004 Add optional `status` enum (`queued | indexing | done | failed`) to `Memory` schema in [spec/omp-0.1.openapi.yaml](../../spec/omp-0.1.openapi.yaml) (FR-122 / data-model.md §1)
- [X] T005 Add `ingestion_timeout` to the `Error.code` enum in [spec/omp-0.1.openapi.yaml](../../spec/omp-0.1.openapi.yaml) (data-model.md §2)
- [X] T006 Mirror `Memory.status: Optional[Literal[...]] = None` in [sdk-python/openmem/types.py](../../sdk-python/openmem/types.py) (FR-122)
- [X] T007 [P] Update [sdk-python/tests/test_types_match_openapi.py](../../sdk-python/tests/test_types_match_openapi.py) to assert the `status` field round-trips between OpenAPI and Pydantic
- [X] T008 [P] Register `code="ingestion_timeout"` handling in [sdk-python/openmem/errors.py](../../sdk-python/openmem/errors.py) (additive — no new exception class; just enum value support)
- [X] T009 Create [sdk-python/openmem/adapters/_ingest.py](../../sdk-python/openmem/adapters/_ingest.py) with `poll_until(fn, timeout, *, base_delay=1.0, max_delay=5.0, provider, on_timeout_details)` helper. Backoff formula MUST be `delay = min(max_delay, base_delay * 2**n)` (n = attempt index from 0). `timeout` MUST be a positive float; non-positive raises `ValueError`. On budget exhaustion raise `ProviderError(code="ingestion_timeout", provider=..., details=...)` (research.md R1; data-model.md §2; used by US1 + US2)
- [X] T010 [US4] Modify [sdk-python/tests/conftest.py](../../sdk-python/tests/conftest.py) to add the live/mock fixture switch: per-provider fixtures (`mem0_adapter`, `supermemory_adapter`, `letta_adapter`) that swap to a real adapter iff `os.environ.get("OMP_LIVE", "").strip() == "1"` AND `os.environ.get("<PROVIDER>_API_KEY", "").strip() != ""`; otherwise return the M2 PostgresAdapter shim unchanged. Env-var names matched case-sensitively. Parse `OMP_INGEST_TIMEOUT` as positive int in `(0, 600]`; on invalid value warn-and-fall-back to 60. API-key values MUST NEVER be logged — only `"<provider> live mode enabled"` (FR-118 / data-model.md §4a)
- [X] T011 [US4] In [sdk-python/tests/conftest.py](../../sdk-python/tests/conftest.py) add per-fixture finalizers that record created memory ids during the test and call `adapter.delete(id)` at teardown; cleanup failures logged at WARNING and never raised (FR-119 / EC-105 / research.md R4)
- [X] T012 [US4] In [sdk-python/tests/conftest.py](../../sdk-python/tests/conftest.py) add a collection hook `pytest_collection_modifyitems` that auto-skips `@pytest.mark.live` tests when `OMP_LIVE != "1"` (FR-121 / data-model.md §6)

**Checkpoint**: OpenAPI + Pydantic agree on `status`; `_ingest.poll_until` is importable; `pytest --collect-only` shows live-marker tests skipped without `OMP_LIVE`; mock-mode fixtures unchanged.

---

## Phase 3: User Story 1 — Mem0 live (Priority: P1) 🎯 MVP

**Goal**: `Mem0Adapter` works end-to-end against `mem0ai>=2.0` against the live API: async ingestion (`status="queued"`), bounded `get` poll, LLM-rewrite preservation via `x-mem0.original_content`.

**Independent Test**: `OMP_LIVE=1 MEM0_API_KEY=… pytest "sdk-python/tests/test_contract_lifecycle.py::test_add_then_search_finds_original_content[mem0]"` passes; `python examples/02_switch_providers.py` prints results from `mem0` alongside `postgres` (SC-101, SC-105).

### Tests for User Story 1 (write FIRST, ensure they FAIL before implementation)

- [X] T013 [P] [US1] Add new contract test `test_add_then_search_finds_original_content` to [sdk-python/tests/test_contract_search.py](../../sdk-python/tests/test_contract_search.py) — adds a memory with content `"omp probe XYZ-{uuid}"`, calls `search` with the original phrase, asserts at least one result references the memory id; runs for every advertised-search adapter (FR-120 / SC-106)
- [X] T014 [P] [US1] Add new contract test `test_status_round_trips` to [sdk-python/tests/test_contract_lifecycle.py](../../sdk-python/tests/test_contract_lifecycle.py) — asserts `Memory.status` is preserved through `add → get → list → search` for every adapter. Sub-cases: (a) value present (e.g. `"done"`) round-trips literally, (b) `status=None` from upstream is acceptable and round-trips as `None` (passthrough-with-legacy-server case) (SC-108)
- [X] T015 [P] [US1] Create [sdk-python/tests/adapters/test_mem0_live.py](../../sdk-python/tests/adapters/test_mem0_live.py) with `@pytest.mark.live` tests covering: (a) `add` returns within 5 s with `status="queued"` (FR-102 acceptance #1), (b) `get(id)` polls and resolves to `status="done"` within `OMP_INGEST_TIMEOUT` (FR-105 / acceptance #2), (c) `get` raises `ProviderError(code="ingestion_timeout")` when timeout elapses (EC-101), (d) LLM-rewrite roundtrip via `x-mem0.original_content` (FR-102 + acceptance #3), (e) empty-rewrite no-op (EC-102), (f) low-information add → `list(user_id)` returns `len(items)==0` and does NOT raise (EC-102 explicit assertion)

### Implementation for User Story 1

- [X] T016 [US1] Rewrite [sdk-python/openmem/adapters/mem0.py](../../sdk-python/openmem/adapters/mem0.py) `__init__` to accept `api_key` + optional `host`, lazy-import `from mem0 import MemoryClient`, lazy-construct on first verb use (FR-101)
- [X] T017 [US1] Rewrite `Mem0Adapter.add` per [contracts/mem0-mapping.md](contracts/mem0-mapping.md): post `messages=[{role:user,content:...}]`, capture `event_id`, return `Memory(id=event_id, content=ORIGINAL, status="queued", x-mem0={event_id, original_content})` (FR-102)
- [X] T018 [US1] Rewrite `Mem0Adapter.get` to use `_ingest.poll_until` around `client.get(memory_id=id)`, default budget `OMP_INGEST_TIMEOUT` (env, default 60); on success return `Memory(status="done", content=resp["memory"], ...)` preserving any cached `x-mem0.original_content`; on timeout raise `ProviderError(code="ingestion_timeout", provider="mem0", details={event_id:id})` (FR-105)
- [X] T019 [US1] Rewrite `Mem0Adapter.list` to call `client.get_all(filters={"user_id": user_id}, version="v2", page=N, limit=L)`. `N` MUST be decoded from cursor via `data-model.md §2a` (base64-urlsafe(json({"page":N})) ); empty/missing cursor → `N=1`; malformed cursor → raise `InvalidRequestError(message="malformed cursor")` BEFORE upstream call. Parse `{count,next,previous,results}`; encode `next_cursor` per §2a when `next` non-null, else `None` (FR-103)
- [X] T020 [US1] Rewrite `Mem0Adapter.search` to call `client.search(query=..., filters={"user_id": user_id}, version="v2", limit=...)`; map `{results:[Memory+score]}` → `[SearchResult(memory=Memory(status="done",...), score=item["score"])]`. If `user_id` is empty/None, raise `InvalidRequestError(message="user_id is required")` BEFORE upstream call (FR-104)
- [X] T021 [US1] Update `Mem0Adapter.update`/`delete` per [contracts/mem0-mapping.md](contracts/mem0-mapping.md) (use `memory_id=` kwarg; treat 404 on delete as idempotent success)
- [X] T022 [US1] Update `Mem0Adapter.capabilities()` to advertise `verbs=["add","get","list","search","update","delete","capabilities"]` and `features={"status_field": True, "async_ingestion": True}`
- [X] T023 [US1] Wire mem0 into the live-mode finalizer registration added in T011 (track `created_ids`; `delete` on teardown)

**Checkpoint**: `OMP_LIVE=1 MEM0_API_KEY=… pytest -k mem0 --no-cov` is green for all advertised verbs; `test_add_then_search_finds_original_content[mem0]` passes; `test_status_round_trips[mem0]` passes.

---

## Phase 4: User Story 2 — Supermemory live (Priority: P2)

**Goal**: `SupermemoryAdapter` works end-to-end against `https://api.supermemory.ai/v3` with camelCase mapping, async ingestion, `POST /memories/list` + `POST /search`.

**Independent Test**: `OMP_LIVE=1 SUPERMEMORY_API_KEY=… pytest -k supermemory` passes for all advertised verbs; demo prints supermemory results (SC-102, SC-105).

### Tests for User Story 2 (write FIRST, ensure they FAIL before implementation)

- [X] T024 [P] [US2] Create [sdk-python/tests/adapters/test_supermemory_live.py](../../sdk-python/tests/adapters/test_supermemory_live.py) with `@pytest.mark.live` tests covering: (a) default `base_url` is `/v3`, overridable by `SUPERMEMORY_BASE_URL` (FR-106 / acceptance #1), (b) `add` posts `{content, metadata:{user_id}}` and returns queued `Memory` (FR-107 / acceptance #2), (c) `list` uses `POST /memories/list` and parses camelCase pagination (FR-108 / acceptance #3), (d) `search` uses `POST /search` and parses chunk-shaped results (FR-109 / acceptance #4), (e) `Memory.user_id` ignores top-level `userId` and reads `metadata.user_id` (FR-110 / EC-103), (f) `update` raises `UnsupportedCapabilityError` without HTTP call (FR-111)
- [X] T025 [P] [US2] Update [sdk-python/tests/adapters/test_supermemory_mapping.py](../../sdk-python/tests/adapters/test_supermemory_mapping.py) (M2's mock-mode unit tests) to assert the camelCase mapping table from [contracts/supermemory-mapping.md](contracts/supermemory-mapping.md)

### Implementation for User Story 2

- [X] T026 [US2] Update [sdk-python/openmem/adapters/supermemory.py](../../sdk-python/openmem/adapters/supermemory.py) default `base_url` to `https://api.supermemory.ai/v3`; honour `SUPERMEMORY_BASE_URL` env override (FR-106)
- [X] T027 [US2] Rewrite `SupermemoryAdapter.add` per [contracts/supermemory-mapping.md](contracts/supermemory-mapping.md) — `POST /memories` with `{content, metadata:{user_id, scope, tags, x-...}}`, parse `{id, status:"queued"}` into `Memory(id=resp["id"], content=ORIGINAL, status="queued", user_id=user_id)` (FR-107)
- [X] T028 [US2] Rewrite `SupermemoryAdapter.get` to `GET /memories/{id}` with `_ingest.poll_until` (budget `OMP_INGEST_TIMEOUT`); on success parse camelCase doc, read `Memory.user_id` from `metadata.user_id` (FR-110); on timeout raise `ProviderError(code="ingestion_timeout", provider="supermemory")`
- [X] T029 [US2] Rewrite `SupermemoryAdapter.list` to `POST /memories/list` body `{limit, page:N, filters:{user_id}}`. `N` MUST be decoded from cursor per data-model.md §2a; malformed cursor → raise `InvalidRequestError` BEFORE HTTP call. Parse `{memories:[doc...], pagination:{currentPage, limit, totalPages}}`; encode `next_cursor` per §2a when `currentPage < totalPages`, else `None` (FR-108)
- [X] T030 [US2] Rewrite `SupermemoryAdapter.search` to `POST /search` body `{q, limit, filters:{user_id}}`; map chunk-shaped response → one `SearchResult` per `documentId` with score = best chunk score. If `user_id` is empty/None, raise `InvalidRequestError(message="user_id is required")` BEFORE HTTP call (FR-109)
- [X] T031 [US2] Update `SupermemoryAdapter.delete` to `DELETE /memories/{id}` (204 / 404 both → `None`)
- [X] T032 [US2] Update `SupermemoryAdapter.capabilities()` to advertise `verbs=["add","get","list","search","delete","capabilities"]` and `features={"status_field": True, "async_ingestion": True}` — `update` excluded (FR-111); ensure calling `update` raises `UnsupportedCapabilityError` BEFORE any HTTP call (FR-009 carry-over)
- [X] T033 [US2] Wire supermemory into the live-mode finalizer registration (track ids; `delete` on teardown)

**Checkpoint**: `OMP_LIVE=1 SUPERMEMORY_API_KEY=… pytest -k supermemory` is green; `test_add_then_search_finds_original_content[supermemory]` passes; `test_status_round_trips[supermemory]` passes.

---

## Phase 5: User Story 3 — Letta live (Priority: P3)

**Goal**: `LettaAdapter` works end-to-end against `letta-client>=1.10`: list-of-passages handling, `top_k=` search, `get`/`update` correctly excluded from capabilities.

**Independent Test**: `OMP_LIVE=1 LETTA_API_KEY=… pytest -k letta` passes for `add/list/search/delete/context`; `get`+`update` correctly skipped via capability-aware hook (SC-103, SC-105).

### Tests for User Story 3 (write FIRST, ensure they FAIL before implementation)

- [X] T034 [P] [US3] Create [sdk-python/tests/adapters/test_letta_live.py](../../sdk-python/tests/adapters/test_letta_live.py) with `@pytest.mark.live` tests covering: (a) `Letta(api_key=...)` constructor (FR-112 / acceptance #1), (b) `add` long-text auto-chunking returns one OMP `Memory` whose `id` is `mem_{agent_id}_{first_passage_id}` and `x-letta.passage_ids` lists ALL passage ids (FR-113 / EC-104 / acceptance #2), (c) `get` raises `UnsupportedCapabilityError` WITHOUT network call (FR-116 / acceptance #3), (d) `search` uses `top_k=limit` and parses `PassageSearchResponse` (FR-115 / acceptance #4), (e) `delete` removes EVERY passage id under `x-letta.passage_ids` (FR-114), (f) `_agent_for(user_id)` cache reuse and invalidate-on-not-found (FR-117)
- [X] T035 [P] [US3] Update [sdk-python/tests/adapters/test_letta_mapping.py](../../sdk-python/tests/adapters/test_letta_mapping.py) (M2's mock-mode unit tests) to assert the call-shape mapping from [contracts/letta-mapping.md](contracts/letta-mapping.md)

### Implementation for User Story 3

- [X] T036 [US3] Update [sdk-python/openmem/adapters/letta.py](../../sdk-python/openmem/adapters/letta.py) `__init__` to construct `Letta(api_key=api_key, base_url=...)` (M2 wrap-up fix already lands `api_key=`; this task confirms parity with `letta-client>=1.10`) (FR-112)
- [X] T037 [US3] Rewrite `LettaAdapter.add` per [contracts/letta-mapping.md](contracts/letta-mapping.md) — call `client.agents.passages.create(agent_id, text=content)`, treat result as `list[Passage]`, take `passages[0].id` as canonical, encode `id = f"mem_{agent_id}_{passages[0].id}"`, stash all passage ids under `x-letta.passage_ids` (FR-113)
- [X] T038 [US3] Rewrite `LettaAdapter.delete` to parse `id` → `(agent_id, _)`, look up `x-letta.passage_ids` (or fall back to the parsed passage id when no record cached), and delete EVERY passage. Determine the correct kwarg via `inspect.signature(passages.delete).parameters` at adapter init: try in order `passage_id`, `id`, `memory_id`; if none match, raise `ProviderError(code="provider_error", provider="letta", message="cannot determine passages.delete kwarg name; pin letta-client version")` (FR-114 / contracts/letta-mapping.md). On partial failure, return success once ≥1 passage deleted; log per-passage failures at WARNING (no key or content data in the log message)
- [X] T039 [US3] Rewrite `LettaAdapter.search` to call `client.agents.passages.search(agent_id, query=query, top_k=limit)` (NOT `limit=`); parse `PassageSearchResponse(count, results=[Result(id, content, timestamp, tags)])` → `[SearchResult(memory=Memory(status="done",...), score=None)]`. Tag filtering is deferred per FR-115; do NOT pass `tags=` (FR-115)
- [X] T040 [US3] Update `LettaAdapter.list` to `client.agents.passages.list(agent_id, limit=limit)`; map each → `Memory(status="done", x-letta.passage_ids=[p.id])`; `next_cursor=None`
- [X] T041 [US3] Update `LettaAdapter.capabilities()` to advertise `verbs=["add","list","search","delete","capabilities"]` (no `get`, no `update`) and `features={"status_field": True, "async_ingestion": False, "auto_chunking": True}`; ensure calling `get` or `update` raises `UnsupportedCapabilityError` BEFORE any network call (FR-116)
- [X] T042 [US3] Implement `_agent_for(user_id)` cache (dict on adapter instance) per FR-117 — first call creates the agent, subsequent calls reuse the cached id; on agent-not-found, invalidate the cache entry and retry once
- [X] T043 [US3] Wire letta into the live-mode finalizer registration: track created agent ids AND passage ids; on teardown delete passages first then agents (FR-119)

**Checkpoint**: `OMP_LIVE=1 LETTA_API_KEY=… pytest -k letta` is green; `get`/`update` skipped via capability-aware hook; `test_add_then_search_finds_original_content[letta]` passes.

---

## Phase 6: User Story 4 — Live & mock coexistence validation (Priority: P2)
**Goal**: Confirm that the foundational test infrastructure built in Phase 2 actually preserves M2's mock-mode baseline and provides per-provider opt-in correctly. (Most US4 work landed in T010–T012; this phase is verification + the rate-limit edge case.)

**Independent Test**: With no env vars set: `pytest sdk-python/tests --no-cov` produces 158 passed / 2 skipped (M2 baseline) plus the new `test_add_then_search_finds_original_content` and `test_status_round_trips` rows passing across all adapters (SC-104).

### Tests for User Story 4 (write FIRST, ensure they FAIL before implementation)

- [X] T044 [P] [US4] Add a meta-test [sdk-python/tests/test_live_mode_switch.py](../../sdk-python/tests/test_live_mode_switch.py) that: (a) with `OMP_LIVE` unset, asserts the `mem0_adapter` fixture returns the PostgresAdapter shim (acceptance #1); (b) with `OMP_LIVE=1` + a fake `MEM0_API_KEY=test` (and the real client patched out), asserts the fixture constructs a real `Mem0Adapter` and the supermemory + letta fixtures still return shims (acceptance #2)
- [X] T045 [P] [US4] Add a meta-test in the same file that: with `OMP_LIVE=1` and a stub adapter that records `delete()` calls, asserts that creating memories during a test triggers `delete` for each id at teardown (acceptance #3 / FR-119)
- [X] T046 [P] [US4] Add `@pytest.mark.live` retry-on-429 test in [sdk-python/tests/adapters/test_mem0_live.py](../../sdk-python/tests/adapters/test_mem0_live.py) — patch the underlying client to raise once with `RateLimitedError(retry_after=1)`, assert the test framework sleeps then retries once before failing (EC-106)
- [X] T046b [P] [US4] Add a meta-test `test_passthrough_mirrors_status` to [sdk-python/tests/adapters/test_passthrough_native.py](../../sdk-python/tests/adapters/test_passthrough_native.py) using `httpx.MockTransport`: stub a `GET /memories/{id}` response carrying `status="indexing"` and assert the OMP `Memory.status` returned by `PassthroughAdapter.get` is exactly `"indexing"`; also stub a response with no `status` field and assert OMP returns `Memory.status=None` (data-model.md §1 passthrough row)
- [X] T046c [P] [US4] Add a meta-test `test_cursor_format_round_trips` to [sdk-python/tests/test_cursor_format.py](../../sdk-python/tests/test_cursor_format.py): assert `_encode_cursor(N) → _decode_cursor(...)` round-trips for N in `[1, 2, 100, 999999]`; assert `_decode_cursor("not-base64")`, `_decode_cursor("")`, `_decode_cursor("<" * 100)`, and `_decode_cursor(<base64 of non-int page>)` all raise `InvalidRequestError`. Also assert `PassthroughAdapter.list(cursor=<malformed>)` raises `InvalidRequestError` BEFORE issuing any HTTP call (use `httpx.MockTransport` and assert zero calls intercepted) — ensures the pagination cursor cannot be used as an injection vector at the passthrough boundary either (data-model.md §2a)
- [X] T046d [P] [US4] Add a meta-test `test_env_var_parsing_safe` to [sdk-python/tests/test_live_mode_switch.py](../../sdk-python/tests/test_live_mode_switch.py): for `OMP_LIVE` in `["", " ", "true", "yes", "0", "1 ", " 1"]` assert mock-mode returned (only `"1"` activates); for `MEM0_API_KEY` in `["", "  ", "\n\t"]` assert mock-mode returned (whitespace-only never activates); for `OMP_INGEST_TIMEOUT` in `["-1", "0", "abc", "700", "1.5"]` assert default 60 used and a warning emitted (data-model.md §4a / FR-118)
- [X] T046e [P] [US4] Add a meta-test `test_no_credentials_in_logs` to [sdk-python/tests/test_live_mode_switch.py](../../sdk-python/tests/test_live_mode_switch.py): with `OMP_LIVE=1` and `MEM0_API_KEY="sk-supersecret-DO-NOT-LEAK"`, run a full mem0-mocked verb cycle while capturing `caplog`; assert `"sk-supersecret"` appears in zero log records (defends against credential exfiltration via debug logging) (FR-118)

### Implementation for User Story 4

- [X] T047 [US4] Add the 429 retry-once helper to [sdk-python/tests/conftest.py](../../sdk-python/tests/conftest.py) — a small wrapper around live-fixture verbs that, on `RateLimitedError`, honours `retry_after` (capped at e.g. 30 s) and retries exactly once (EC-106)
- [X] T048 [US4] Document the live-mode contract in the existing [.env.example](../../.env.example) header comment (already partly there from T003; this task confirms the table of (`OMP_LIVE`, `MEM0_API_KEY`, `SUPERMEMORY_API_KEY`, `LETTA_API_KEY`) and what each switch enables)

**Checkpoint**: `pytest sdk-python/tests --no-cov` (no env vars) → 158 passed / 2 skipped + new contract tests passing; `OMP_LIVE=1 MEM0_API_KEY=fake pytest tests/test_live_mode_switch.py` exercises the per-provider opt-in path; cleanup finalizers verified.

---

## Phase 7: Polish & Cross-Cutting Concerns

**Purpose**: Demo update, changelog, manual-run validation, no-leak verification.

- [X] T049 [P] Update [examples/02_switch_providers.py](../../examples/02_switch_providers.py) to: (a) skip any provider whose key is missing, (b) print `id`, `status`, and search-result count per provider, (c) handle `UnsupportedCapabilityError` gracefully (Letta has no `get`)
- [X] T050 [P] Add a `## M2.1` section to [CHANGELOG.md](../../CHANGELOG.md): note `Memory.status` (additive), `code="ingestion_timeout"` (additive), per-adapter rewrites, mem0ai 2.x + letta-client 1.10 pins, supermemory `/v3` default, breaking-for-async-callers semantic (`add` no longer guarantees `status="done"` for mem0/supermemory)
- [ ] T051 Run `python examples/02_switch_providers.py` with all three keys set; confirm exit 0 and non-empty search results for `postgres`, `mem0`, `supermemory`, `letta` (SC-105)
- [ ] T052 Run a full live suite (`OMP_LIVE=1 <all keys> pytest sdk-python/tests --no-cov`); confirm SC-101/SC-102/SC-103 pass and finalizers leave provider state empty. Verification mechanism: for each provider, after the suite teardown completes, call `adapter.list(user_id=<test_user>).items` and assert `len == 0`. Also `grep -i "api_key\|api-key\|bearer " pytest.log` MUST return zero matches (no leaked credentials in logs) (SC-107)
- [ ] T053 Walk through [quickstart.md](quickstart.md) end-to-end on a clean checkout (mock mode then live mode); fix any drift discovered

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies — start immediately.
- **Foundational (Phase 2)**: Depends on Setup. **BLOCKS all user stories.** T004 (OpenAPI spec change) MUST land before T006 (Pydantic mirror) per Principle I.
- **US1 / US2 / US3 (Phases 3–5)**: All depend on Foundational. Independent of each other — can proceed in parallel by different team members.
- **US4 (Phase 6)**: Depends on Foundational + at least one of US1/US2/US3 having a real adapter to switch to.
- **Polish (Phase 7)**: Depends on US1 + US2 + US3 + US4 all complete.

### Within-Phase Dependencies

- **Phase 2**: T004 → T006 → T007 (spec → Pydantic → round-trip test). T009 (`_ingest`) is independent and parallel-safe.
- **Phase 3 (US1)**: T013–T015 (tests) FIRST and FAIL → T016 → {T017, T018, T019, T020} (verbs) → T021 (delete/update) → T022 (capabilities) → T023 (finalizer wiring).
- **Phase 4 (US2)**: T024–T025 (tests) FIRST and FAIL → T026 (base URL) → {T027, T028, T029, T030, T031} (verbs) → T032 (capabilities) → T033 (finalizer wiring).
- **Phase 5 (US3)**: T034–T035 (tests) FIRST and FAIL → T036 (constructor) → {T037, T038, T039, T040} (verbs) → T041 (capabilities) → T042 (agent cache) → T043 (finalizer wiring).
- **Phase 6 (US4)**: T044–T046 (tests) FIRST → T047 → T048.

### Parallel Opportunities

- All Phase 1 tasks (T001, T002, T003) parallel — different files.
- In Phase 2: T007 + T008 + T009 parallel after T004–T006 land. T010–T012 all touch `conftest.py` so SERIAL within `conftest.py` but parallel-safe relative to T007–T009.
- Phases 3, 4, 5 entirely parallel by team member after Phase 2 completes (each story owns its own adapter file + live-test file).
- Within each user-story phase, all `[P]` test-file creation tasks are parallel (different files).
- Polish: T049 + T050 parallel (different files).

---

## Parallel Example: Foundational Phase

```powershell
# After T004–T006 commit:
# Run T007, T008, T009 simultaneously (different files):
#   T007 → sdk-python/tests/test_types_match_openapi.py
#   T008 → sdk-python/openmem/errors.py
#   T009 → sdk-python/openmem/adapters/_ingest.py
# Then run T010, T011, T012 (all in conftest.py — SEQUENTIAL):
#   T010 → fixture switch
#   T011 → finalizers
#   T012 → live-marker collection hook
```

## Parallel Example: User Story 1 — MVP

```powershell
# After Phase 2 done. All [P] tests authored in parallel:
#   T013 → tests/test_contract_search.py (new test row)
#   T014 → tests/test_contract_lifecycle.py (new test row)
#   T015 → tests/adapters/test_mem0_live.py (NEW)
# Verify all FAIL (no impl yet). Then implementation T016 → T023 sequentially.
```

---

## Implementation Strategy

**MVP scope** = Phase 1 + Phase 2 + Phase 3 (US1 only). At MVP, mem0 is the only live-capable adapter; supermemory and letta remain in M2 mock mode. This delivers SC-101 + SC-104 + SC-105 (partial) + SC-106 (mem0 row) + SC-108 (mem0 row) + SC-109.

**Incremental delivery**:

1. **Ship MVP** (mem0 live) → cut release `0.2.1-rc1`. The ingestion-timeout + LLM-rewrite story is the most novel work; landing it in isolation de-risks the rest.
2. **Add US2** (supermemory live) → release `0.2.1-rc2`. Validates that the `_ingest.poll_until` helper generalises beyond mem0.
3. **Add US3** (letta live) → release `0.2.1-rc3`. Validates the capability-aware skip mechanism on a real provider that legitimately lacks verbs.
4. **Land US4 + Polish** → cut `0.2.1` GA. The meta-tests + the demo + CHANGELOG.

**Why this order**: P1 first per spec; US4 (mock-mode preservation) is verified incidentally by every other phase's `pytest --no-cov` run, so it ships last as formal validation rather than gating earlier phases.

---

## Format Validation

Every task above conforms to: `- [ ] T### [P?] [US?] Description with file path`

- ✅ All tasks have a checkbox (`- [ ]`).
- ✅ All tasks have a sequential ID (T001–T053, plus T046b/c/d/e for hardening).
- ✅ All [P] markers are on tasks that touch different files from any other in-flight task.
- ✅ All user-story phase tasks (T010–T012, T013–T023, T024–T033, T034–T043, T044–T048) carry `[US1]`/`[US2]`/`[US3]`/`[US4]` labels.
- ✅ Setup, Foundational (non-US4), and Polish tasks omit the `[USx]` label per template.
- ✅ Every task references a concrete file path (or files via the contracts/data-model docs).

**Total tasks**: 58.
**Per-story counts**: Setup 3, Foundational 6 + US4 3 = 9, US1 11, US2 10, US3 10, US4 10, Polish 5.
**Parallel opportunities**: 24 [P]-marked tasks across all phases.
