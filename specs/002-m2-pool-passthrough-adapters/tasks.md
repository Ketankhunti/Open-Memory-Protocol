# Tasks: M2 — Connection pooling, native passthrough, first translation adapters

**Feature Branch**: `002-m2-pool-passthrough-adapters`
**Spec**: [spec.md](spec.md) · **Plan**: [plan.md](plan.md) · **Research**: [research.md](research.md) · **Data model**: [data-model.md](data-model.md) · **Contracts**: [contracts/](contracts/)

**Tests**: Required by Constitution Principle II (NON-NEGOTIABLE) — every adapter MUST pass the parametrized contract suite, and per-test timeout enforces SC-006. Tests are written before implementation per the Red→Green→Refactor rule.

**Organization**: Tasks are grouped by user story (US1/US2/US3) so each can be implemented and shipped independently. US1 = MVP.

## Format: `[ID] [P?] [Story] Description`

- **[P]** = parallelizable (different files, no incomplete-task dependency)
- **[US1]/[US2]/[US3]** = user story label (only on story-phase tasks)

---

## Phase 1: Setup (Shared Infrastructure)

- [X] T001 Add `psycopg-pool>=3.2` to `[project.dependencies]` in [sdk-python/pyproject.toml](../../sdk-python/pyproject.toml)
- [X] T002 Add `pytest-timeout>=2.3` to `[project.optional-dependencies].dev` in [sdk-python/pyproject.toml](../../sdk-python/pyproject.toml) (FR-017)
- [X] T003 Add `timeout = 30` to `[tool.pytest.ini_options]` in [sdk-python/pyproject.toml](../../sdk-python/pyproject.toml) (R-006, SC-006)
- [X] T004 Add three opt-in extras to [sdk-python/pyproject.toml](../../sdk-python/pyproject.toml): `mem0 = ["mem0ai>=0.1"]`, `supermemory = []` (REST-only marker extra), `letta = ["letta-client>=0.1"]`
- [X] T005 [P] Add a CHANGELOG `## [0.2.0] — 2026-04-28` skeleton entry in [CHANGELOG.md](../../CHANGELOG.md) listing the four sections to be filled by US1/US2/US3 tasks (RLock removal note included per EC-009)

---

## Phase 2: Foundational (Blocking Prerequisites)

⚠️ Must complete before any US phase begins.

- [X] T006 Create [sdk-python/openmem/adapters/_http.py](../../sdk-python/openmem/adapters/_http.py): shared `httpx.Client` factory + `decode_omp_error(response, provider)` helper that implements the order-of-evaluation rules from [contracts/passthrough-http.md](contracts/passthrough-http.md) §"Error mapping" (envelope → 4xx → 5xx → transport). Used by passthrough and translation adapters.
- [X] T007 [P] Reserve `tests/adapters/fixtures/` directory under [sdk-python/tests/adapters](../../sdk-python/tests/adapters) with a single `.gitkeep`. Currently no JSON fixtures are required (R-004 was revised to use inline MagicMock / MockTransport setup); this directory is kept as the documented home should any adapter later need on-disk recordings.
- [X] T008 (DEFERRED to start of Phase 4 — US1 does not require it.) Add an `_omp_mock_server` session-scoped fixture to [sdk-python/tests/conftest.py](../../sdk-python/tests/conftest.py) implementing an in-process OMP HTTP shim built on `httpx.MockTransport` and a dispatch dict keyed by `(method, path-template)`. The shim delegates writes to a fresh `PostgresAdapter` instance per session so it round-trips real data. Used by passthrough tests (R-002).

**Checkpoint**: shared HTTP utilities + mock OMP server are available; user stories may now proceed in parallel.

---

## Phase 3: User Story 1 — Postgres adapter scales under concurrent load (Priority: P1) 🎯 MVP

**Goal**: Replace M1's `@_synchronized` RLock with a `psycopg_pool.ConnectionPool`. Concurrent verb calls flow through separate pooled connections; the M1 5-minute hang is gone; throughput rises ≥5×.

**Independent Test**: `pytest sdk-python/tests/adapters/test_postgres_pool.py -q` passes; `tests/adapters/test_postgres_specific.py::test_concurrent_inserts_do_not_deadlock` finishes in <30s; the existing contract suite stays green for the postgres adapter.

### Tests for User Story 1 (write FIRST; must fail before T013–T016 land)

- [ ] T009 [P] [US1] Add [sdk-python/tests/adapters/test_postgres_pool.py](../../sdk-python/tests/adapters/test_postgres_pool.py) with the following tests, each using the existing `pg_url` fixture:
  - `test_pool_kwargs_accepted_with_defaults` — instantiate `PostgresAdapter(url=...)` (no pool kwargs) and assert it works (FR-002).
  - `test_pool_size_caps_concurrency` — `pool_max_size=3`, fire 3 long-running verbs concurrently, assert exactly 3 connections appear via `pool.get_stats()`.
  - `test_pool_exhaustion_raises_provider_error` — `pool_max_size=1, pool_timeout=0.5`, hold one connection, second call raises `ProviderError` containing "exhausted" within 1s (FR-004, EC-001).
  - `test_pool_recycles_broken_connection` — manually close an underlying connection from inside a verb, assert next verb call still succeeds with no leaked errors (FR-005).
  - `test_pool_5x_throughput` — marked `@pytest.mark.timeout(120)`, runs 200 concurrent `mem.add()` against `pool_max_size=10` and asserts wall time ≤ (M1_baseline / 5); M1 baseline is hard-coded at 60s (the observed pre-fix value) so the assertion is `wall_time < 12.0` (SC-001).
  - `test_no_lock_attribute_on_adapter` — `assert not hasattr(adapter, "_lock")` (FR-003, EC-009).
  - `test_first_call_works_without_warmup` — instantiate adapter, call `mem.add()` immediately, assert it succeeds before any prior verb call (EC-002).

### Implementation for User Story 1

- [X] T010 [US1] In [sdk-python/openmem/adapters/postgres.py](../../sdk-python/openmem/adapters/postgres.py): remove `import threading`, remove `from functools import wraps`, remove the module-level `_synchronized` decorator, remove `self._lock = threading.RLock()` from `__init__`, and remove every `@_synchronized` decoration from `add/get/update/delete/list/search/context` (FR-003).
- [X] T011 [US1] In [sdk-python/openmem/adapters/postgres.py](../../sdk-python/openmem/adapters/postgres.py) `__init__`: add kwargs `pool_min_size: int = 1, pool_max_size: int = 10, pool_timeout: float = 30.0`; replace `self._conn = psycopg.connect(url)` with `self._pool = psycopg_pool.ConnectionPool(conninfo=url, min_size=pool_min_size, max_size=pool_max_size, timeout=pool_timeout, open=True)`; remove `self._conn`. (FR-001, FR-002)
- [X] T012 [US1] In [sdk-python/openmem/adapters/postgres.py](../../sdk-python/openmem/adapters/postgres.py): refactor every verb body (`add/get/update/delete/list/search/context` and `_ensure_schema`) to acquire connections via `with self._pool.connection() as conn:` then `with conn.cursor() as cur:`. Remove all uses of `self._conn`.
- [X] T013 [US1] In [sdk-python/openmem/adapters/postgres.py](../../sdk-python/openmem/adapters/postgres.py): wrap `psycopg_pool.PoolTimeout` → `ProviderError("connection pool exhausted", provider="postgres")`. (FR-004, EC-001)
- [X] T014 [US1] In [sdk-python/openmem/adapters/postgres.py](../../sdk-python/openmem/adapters/postgres.py): add `close(self) -> None` that calls `self._pool.close()`; idempotent (D-001 invariant). Update `__init__.py` to export if not already.
- [X] T015 [US1] In [CHANGELOG.md](../../CHANGELOG.md): fill the "Pooling" subsection of the `0.2.0` entry, calling out RLock removal as a behavior change (no public API break) per EC-009.

**Checkpoint**: US1 done — Postgres adapter scales; contract suite passes; concurrency test <30s.

---

## Phase 4: User Story 2 — Native OMP passthrough (Priority: P2)

**Goal**: `PassthroughAdapter` implements every OMP verb over httpx per the OpenAPI spec.

**Independent Test**: `pytest sdk-python/tests/adapters/test_passthrough_native.py -q` plus `pytest sdk-python/tests/test_contract_*.py -q -k passthrough` (after fixture wiring in T021) — all green.

### Tests for User Story 2 (FIRST)

- [X] T016 [P] [US2] Add [sdk-python/tests/adapters/test_passthrough_native.py](../../sdk-python/tests/adapters/test_passthrough_native.py) using `httpx.MockTransport`:
  - `test_each_verb_hits_correct_method_and_path` — table-driven against the [contracts/passthrough-http.md](contracts/passthrough-http.md) verb table; assert method, URL, body shape per verb.
  - `test_authorization_header_when_api_key_set` — assert `Authorization: Bearer …` is sent (FR-011).
  - `test_authorization_header_omitted_when_no_key` — no header.
  - `test_omp_error_envelope_dispatches_to_subclass` — server returns `{"code":"not_found",...}` 404, adapter raises `NotFoundError` (FR-008).
  - `test_4xx_no_envelope_becomes_invalid_request_error` — server returns plain 422, adapter raises `InvalidRequestError` (FR-010, EC-004).
  - `test_5xx_no_envelope_becomes_provider_error` — server returns plain 503, adapter raises `ProviderError` (FR-010, EC-004).
  - `test_capability_gate_raises_before_network` — capabilities advertise `verbs=["add","get"]`; calling `mem.audit()` raises `UnsupportedCapabilityError` and the MockTransport call counter stays at 1 (the probe) (FR-009, EC-003).
  - `test_advertised_verb_returning_501_raises_unsupported_capability_and_does_not_mutate_cache` — capabilities advertise `audit`; remote returns HTTP 501 with no envelope; adapter raises `UnsupportedCapabilityError` and `mem.capabilities().verbs` still contains `audit` afterwards (EC-003).
  - `test_single_redirect_followed` and `test_redirect_loop_raises_provider_error` (EC-004).
  - `test_api_key_never_logged` — install `caplog`, set api_key to a recognizable sentinel, run a verb, assert sentinel not present in any captured log record (FR-011).
  - `test_delete_returns_none_on_204`.

### Implementation for User Story 2

- [X] T017 [US2] Rewrite [sdk-python/openmem/adapters/passthrough.py](../../sdk-python/openmem/adapters/passthrough.py): keep the `_probe` classmethod; replace each `_stub` verb with a real implementation per [contracts/passthrough-http.md](contracts/passthrough-http.md). Use a persistent `httpx.Client` stored on `self._client` constructed in `__init__` (or accepted via `transport=` kwarg for tests). Use the helpers from `_http.py` (T006).
- [X] T018 [US2] In [sdk-python/openmem/adapters/passthrough.py](../../sdk-python/openmem/adapters/passthrough.py): add a private `_check_verb(verb: str)` method that raises `UnsupportedCapabilityError` when `verb not in self._capabilities.verbs`; call it as the first line of every verb method (FR-009, EC-003).
- [X] T019 [US2] In [sdk-python/openmem/adapters/passthrough.py](../../sdk-python/openmem/adapters/passthrough.py): implement body serialization with `model.model_dump(mode="json", exclude_none=True)` and response parsing with `Model.model_validate(response.json())` per [contracts/passthrough-http.md](contracts/passthrough-http.md) §"Body serialization rules" (FR-007).
- [X] T020 [US2] In [sdk-python/openmem/adapters/passthrough.py](../../sdk-python/openmem/adapters/passthrough.py): add `close(self)` that closes `self._client`; ensure `User-Agent: openmem-python/{version}` header is set on the client.
- [X] T021 [US2] In [sdk-python/tests/conftest.py](../../sdk-python/tests/conftest.py): add `passthrough_adapter` module-scoped fixture that constructs `PassthroughAdapter(base_url="http://omp.test", transport=_omp_mock_server)`; append `"passthrough"` to the `adapter` fixture's `params` and dispatch dict. **Do not edit any `test_contract_*.py` file** (SC-005).
- [X] T022 [US2] In [CHANGELOG.md](../../CHANGELOG.md): fill the "Native passthrough" subsection of the `0.2.0` entry.

**Checkpoint**: US2 done — `Memory(base_url=...)` round-trips every verb; passthrough is in the conformance matrix.

---

## Phase 5: User Story 3 — Three real translation adapters (Priority: P3)

**Goal**: Mem0, Supermemory, Letta each pass the contract suite for their advertised verbs.

**Independent Test**: `pytest sdk-python/tests -q -k "mem0 or supermemory or letta"` is fully green in mock mode (default); each adapter's mismatch verbs raise `UnsupportedCapabilityError` (no false greens, no false reds — SC-004).

### Tests for User Story 3 (FIRST — one mapping unit-test file per adapter)

- [X] T023 [P] [US3] Add [sdk-python/tests/adapters/test_mem0_mapping.py](../../sdk-python/tests/adapters/test_mem0_mapping.py): mock the `mem0ai` client (e.g. via `unittest.mock.MagicMock` patched into the adapter's module). Cover:
  - `test_add_maps_inputs_per_table` (verb mapping rows in [contracts/mem0-mapping.md](contracts/mem0-mapping.md))
  - `test_capabilities_matches_table`
  - `test_unsupported_audit_raises_unsupported_capability`
  - `test_provider_errors_translate` — patch the mocked client to raise each Mem0 exception class; assert OMP subclass is raised (FR-014, SC-007).
  - `test_pagination_cursor_round_trips` (EC-005).
  - `test_scope_round_trips_via_tag_prefix` — `add(scope="coding/preferences", ...)` then `get(id)` returns the same scope; mocked client's `metadata` field shows the `__scope:` tag-prefix marker (EC-006).
  - `test_embedding_model_omitted_when_provider_managed` — assert mocked client.add call args do NOT contain `embedding_model`; capabilities flag (`vector_search=True` with provider-managed embeddings) reports correctly (EC-007).
  - `test_x_mem0_extension_round_trips` (Principle V).
- [X] T024 [P] [US3] Add [sdk-python/tests/adapters/test_supermemory_mapping.py](../../sdk-python/tests/adapters/test_supermemory_mapping.py): same shape, using `httpx.MockTransport` for the REST mapping per [contracts/supermemory-mapping.md](contracts/supermemory-mapping.md). Cover all status-code → exception rows; `test_update_not_advertised_raises_unsupported_capability`; `test_scope_round_trips_via_tag_prefix` (EC-006); `test_embedding_model_omitted_when_provider_managed` (EC-007).
- [X] T025 [P] [US3] Add [sdk-python/tests/adapters/test_letta_mapping.py](../../sdk-python/tests/adapters/test_letta_mapping.py): mock `letta-client`; cover OMP-id ↔ `(agent_id, passage_id)` encoding/decoding; `test_capabilities_matches_table`; provider-error translation; `test_one_agent_per_user_id_cached`.

### Implementation for User Story 3

- [X] T026 [P] [US3] Create [sdk-python/openmem/adapters/mem0.py](../../sdk-python/openmem/adapters/mem0.py) implementing `Mem0Adapter(BaseAdapter)` per [contracts/mem0-mapping.md](contracts/mem0-mapping.md). Lazy `import mem0ai` inside `__init__`; raise a clear `ImportError` with `pip install openmem[mem0]` hint if missing. Implement `_to_provider_input`, `_from_provider_output`, `_translate_error`, hard-coded `capabilities()`, all verbs.
- [X] T027 [P] [US3] Create [sdk-python/openmem/adapters/supermemory.py](../../sdk-python/openmem/adapters/supermemory.py) implementing `SupermemoryAdapter(BaseAdapter)` per [contracts/supermemory-mapping.md](contracts/supermemory-mapping.md). Use shared `_http.py` (T006). `update` not advertised — calling raises `UnsupportedCapabilityError`.
- [X] T028 [P] [US3] Create [sdk-python/openmem/adapters/letta.py](../../sdk-python/openmem/adapters/letta.py) implementing `LettaAdapter(BaseAdapter)` per [contracts/letta-mapping.md](contracts/letta-mapping.md). Lazy `import letta_client`; implement `_agent_for(user_id)` cache and `mem_{agent_id}_{passage_id}` id encoding.
- [X] T029 [US3] In [sdk-python/openmem/memory.py](../../sdk-python/openmem/memory.py) `_resolve_adapter`: add three branches `if provider == "mem0": ...`, `if provider == "supermemory": ...`, `if provider == "letta": ...` returning the matching adapter constructed from `**config`. Append `"mem0"`, `"supermemory"`, `"letta"` to `TRANSLATION_ADAPTERS`. (Depends on T026, T027, T028.)
  - `mem0`: `api_key` (required), `host` (optional, default `https://api.mem0.ai`).
  - `supermemory`: `api_key` (required), `base_url` (optional, default `https://api.supermemory.ai/v1`).
  - `letta`: `api_key` (required), `base_url` (optional, default Letta cloud).
- [X] T030 [US3] In [sdk-python/tests/conftest.py](../../sdk-python/tests/conftest.py): add `mem0_adapter`, `supermemory_adapter`, `letta_adapter` module-scoped fixtures; selection logic per fixture:
  ```
  if env var present (e.g. MEM0_API_KEY): instantiate real adapter → live mode
  elif optional extra installed (e.g. `import mem0ai`): instantiate adapter with patched transport / MagicMock SDK → mock mode (default)
  else: pytest.skip("install openmem[mem0] to run this fixture")
  ```
  Append the three names to the `adapter` fixture `params` and dispatch dict. **No edits in `test_contract_*.py`** (SC-005).
- [X] T031 [US3] In [CHANGELOG.md](../../CHANGELOG.md): fill the "Translation adapters" subsection of the `0.2.0` entry, listing the three new adapters and their capability matrices.

**Checkpoint**: US3 done — all three providers in the conformance matrix; total = 5 adapters (postgres, passthrough, mem0, supermemory, letta).

---

## Phase 6: Polish & Cross-Cutting

- [ ] T032 [P] Update [examples/02_switch_providers.py](../../examples/02_switch_providers.py): replace the two `provider="postgres"` calls with one `provider="postgres"` and one `provider="mem0"`, falling back to `Memory(base_url="...", transport=...)` against the in-process shim if `MEM0_API_KEY` is unset (R-007, FR-018, SC-008).
- [ ] T033 [P] Append a "Switching providers" subsection to [sdk-python/README.md](../../sdk-python/README.md) and a top-level "Available adapters" matrix in [README.md](../../README.md) listing all five adapters and their tier per the table in [research.md](research.md) §R-005.
- [ ] T034 [P] Add the "Tooling" subsection of the `0.2.0` entry in [CHANGELOG.md](../../CHANGELOG.md) covering `pytest-timeout` (FR-017) and the example refresh (FR-018).
- [ ] T035 Run `pytest sdk-python/tests -q --no-cov` and confirm:
  - 0 failures, 0 hangs (SC-002)
  - Full suite under 5 minutes (SC-006)
  - `git diff --stat HEAD~5 sdk-python/tests/test_contract_*.py` is empty (SC-005)
- [ ] T036 Run `python examples/02_switch_providers.py` and confirm both providers print comparable output (SC-008).
- [ ] T037 Delete [/memories/repo/m2-followups.md](/memories/repo/m2-followups.md) — items (1)–(4) are now shipped.

---

## Dependencies & Execution Order

### Phase order
1. **Phase 1 Setup** — no deps
2. **Phase 2 Foundational** — depends on Phase 1; **blocks** Phase 3/4/5
3. **Phase 3 (US1)**, **Phase 4 (US2)**, **Phase 5 (US3)** — independent of each other; can run in parallel after Phase 2
4. **Phase 6 Polish** — depends on all desired US phases

### Critical task-level dependencies
- T010–T014 (US1 impl) all touch `postgres.py`; sequential within US1.
- T017–T020 (US2 impl) all touch `passthrough.py`; sequential within US2.
- T026/T027/T028 are independent files → fully parallel.
- T029 depends on T026+T027+T028 (memory.py registers all three).
- T030 depends on T026+T027+T028 (fixtures import the adapters).
- T021 (passthrough fixture) depends on T017–T020.
- T035 depends on every prior task.

### Parallel opportunities
- **Within Setup**: T001+T002+T003+T004 are one file but tiny, then T005 is parallel.
- **Within Foundational**: T007 parallel with T006 and T008.
- **Across user stories** (after Phase 2): one engineer per US.
- **Within US3 implementation**: T026, T027, T028 in parallel.
- **Within US3 tests**: T023, T024, T025 in parallel.
- **Within Polish**: T032, T033, T034 in parallel.

---

## Parallel Example: User Story 3

```bash
# After Phase 2 done, three engineers (or three terminals) can run in parallel:
git worktree add ../m2-mem0 HEAD && cd ../m2-mem0   # T023 + T026
git worktree add ../m2-super HEAD && cd ../m2-super # T024 + T027
git worktree add ../m2-letta HEAD && cd ../m2-letta # T025 + T028
# then merge and run T029 + T030 sequentially on the integration branch.
```

---

## Implementation Strategy

**MVP** = US1 only (Phase 1 + Phase 2 + Phase 3). Ship as `0.2.0a1`:
- Removes the M1 RLock hang.
- 5× throughput improvement.
- Existing examples / contract suite still pass.

**Increment 2** = + US2 (Phase 4). Ship as `0.2.0a2`:
- `Memory(base_url=...)` works for any OMP-conformant server.

**Increment 3** = + US3 (Phase 5). Ship as `0.2.0`:
- Three new providers in the matrix.

**Final** = + Polish (Phase 6). Ship as `0.2.0` final.

Each increment is independently usable and ships value without waiting for the next.

---

## Format validation

All 37 tasks above follow `- [ ] TID [P?] [Story?] description with file path`.
- Setup (T001–T005): no story label ✅
- Foundational (T006–T008): no story label ✅
- US1 (T009–T015): all carry `[US1]` ✅
- US2 (T016–T022): all carry `[US2]` ✅
- US3 (T023–T031): all carry `[US3]` ✅
- Polish (T032–T037): no story label ✅
- Every task names a concrete file path or runnable command ✅
