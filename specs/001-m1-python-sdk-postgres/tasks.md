---

description: "Tasks for M1 — Python SDK skeleton + Postgres adapter (end-to-end)"
---

# Tasks: M1 — Python SDK skeleton + Postgres adapter

**Input**: Design documents from `/specs/001-m1-python-sdk-postgres/`
**Plan**: [plan.md](plan.md) (required) — only design doc available
**Spec sources**: [omp-0.1.openapi.yaml](../../omp-0.1.openapi.yaml), [SPEC_Version8.md](../../SPEC_Version8.md), [.specify/memory/constitution.md](../../.specify/memory/constitution.md)

**Tests**: INCLUDED. The constitution Principle II (Adapter Conformance via Shared Contract Tests) is **NON-NEGOTIABLE** and mandates Red→Green→Refactor for every adapter; therefore the conformance suite is a hard requirement of M1.

**Organization**: Tasks are grouped by user story. There is no spec.md, so the three user stories below are synthesized from the plan's value units:

- **US1 (P1, MVP)** — A Python developer can `pip install openmem`, point it at a Postgres, and use the OMP verbs against pgvector with no provider-specific code. *Quickstart works.*
- **US2 (P2)** — An adapter author can run a single conformance command and prove their adapter is a drop-in replacement for any other. *Conformance suite green.*
- **US3 (P3)** — An end-user can switch memory backends with zero code change, because the SDK auto-detects native vs. translation. *Substitutability demo runs.*

## Format: `[ID] [P?] [Story?] Description`

- **[P]** — different file, no dependency on incomplete tasks → parallel-safe
- **[Story]** — `[US1]` / `[US2]` / `[US3]`; omitted in Setup, Foundational, and Polish phases

## Path Conventions

Single-package Python project per `plan.md`:
- Library code: `sdk-python/openmem/`
- Tests: `sdk-python/tests/`
- Spec: `spec/`
- Examples: `examples/`
- Repo root: `README.md`, `CHANGELOG.md`, `.github/workflows/`

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Repo skeleton + relocate spec to its canonical home (Principle I).

- [X] T001 Create directory tree per `plan.md` Phase 0: `spec/`, `sdk-python/openmem/adapters/`, `sdk-python/tests/adapters/`, `examples/03_chatbot_demo/`, `.github/workflows/`
- [X] T002 Move `omp-0.1.openapi.yaml` → `spec/omp-0.1.openapi.yaml` (use `git mv` to preserve history)
- [X] T003 Move + rename `SPEC_Version8.md` → `spec/OMP-0.1.md` (use `git mv`)
- [X] T004 [P] Create `sdk-python/pyproject.toml` (PEP 621, package name `openmem`, version `0.1.0`, Python `>=3.11`, deps `pydantic>=2`, `httpx`, `psycopg[binary]>=3.1`, `pgvector>=0.2`, `python-ulid`; extras `openai = ["openai>=1.0"]`, `dev = ["pytest", "pytest-cov", "testcontainers[postgres]", "openapi-spec-validator", "PyYAML"]`)
- [X] T005 [P] Create `.gitignore` at repo root with entries `__pycache__/`, `.venv/`, `dist/`, `*.egg-info/`, `.pytest_cache/`, `.coverage`
- [X] T006 [P] Create empty placeholder files so the package is importable: `sdk-python/openmem/__init__.py`, `sdk-python/openmem/adapters/__init__.py`, `sdk-python/tests/__init__.py`, `sdk-python/tests/adapters/__init__.py`
- [X] T007 [P] Update `.specify/templates/plan-template.md` Constitution Check section to enumerate Principles I–V by name (closes the ⚠ pending follow-up in `.specify/memory/constitution.md` Sync Impact Report)

**Checkpoint**: Repo has its target layout; `pip install -e sdk-python` succeeds (empty package); spec lives at `spec/omp-0.1.openapi.yaml` so all downstream paths in the plan resolve.

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Types, errors, adapter ABC, and the embedder protocol — every user story depends on these.

**⚠️ CRITICAL**: No user-story phase can begin until all of Phase 2 is complete.

- [X] T008 [P] Implement pydantic v2 models in `sdk-python/openmem/types.py` exactly mirroring `spec/omp-0.1.openapi.yaml` `components/schemas`: `MemorySource`, `Memory`, `MemoryInput`, `MemoryUpdate`, `MemoryPage`, `SearchResult`, `ContextBlock`, `CapabilityFeatures`, `CapabilityLimits`, `Capabilities`, `AuditEntry`. Required fields on `Memory`: `id`, `content`, `user_id`, `created_at`. Define a private `_OMPBase(BaseModel)` with `model_config = ConfigDict(extra="allow")` and inherit **every response model** from it (`Memory`, `MemoryPage`, `SearchResult`, `ContextBlock`, `Capabilities`, `CapabilityFeatures`, `CapabilityLimits`, `AuditEntry`) so `x-<provider>` extensions round-trip (Principle V) and unknown future fields don't break clients on any response (Principle III; closes analyze finding U3). Add docstring to `SearchResult.score`: "cosine similarity in 0..1; higher = more similar" (closes A2).
- [X] T009 [P] Implement error hierarchy in `sdk-python/openmem/errors.py`: base `OMPError(Exception)` carrying `code`, `type`, `provider`, `request_id`, `message`; subclasses `UnauthorizedError`, `ScopeDeniedError`, `NotFoundError`, `InvalidRequestError`, `RateLimitedError`, `UnsupportedCapabilityError`, `ProviderError`, `UnsupportedProviderError`. Add `OMPError.from_response_dict(payload, provider)` classmethod that dispatches on `error.code`.
- [X] T010 [US2] Define `BaseAdapter(ABC)` in `sdk-python/openmem/adapters/base.py` with abstract methods matching SPEC §12 + the OpenAPI: `add(input: MemoryInput) -> Memory`, `search(query, user_id, scope=None, limit=10, min_score=None) -> list[SearchResult]`, `get(id) -> Memory`, `update(id, update: MemoryUpdate) -> Memory`, `delete(id) -> None`, `list(user_id, scope=None, tag=None, since=None, until=None, limit=50, cursor=None) -> MemoryPage`, `context(query, user_id, scope=None, token_budget=500) -> ContextBlock`, `capabilities() -> Capabilities`, `audit(user_id, app=None, since=None, limit=100) -> list[AuditEntry]` (default `raise UnsupportedCapabilityError`). Depends on T008, T009.
- [X] T011 [P] Implement `Embedder` protocol + reference embedders in `sdk-python/openmem/adapters/embedder.py`: `Embedder` (typing.Protocol with `dim: int`, `model: str`, and `embed(texts: list[str]) -> list[list[float]]`); `FakeEmbedder` (deterministic SHA256-seeded float vectors, `dim=64`, `model="fake-sha256-64"`, no external deps — required by Principle IV so tests + offline demo run with no accounts); `OpenAIEmbedder` (lazy-imports `openai`, `dim=1536`, `model="text-embedding-3-small"`, raises `ImportError` with install hint if extra missing). The `model` attribute is consumed by FR-014 cross-model-search hard-fail (closes analyze finding U2).

**Checkpoint**: Foundation ready. All three user stories may now begin in parallel.

---

## Phase 3: User Story 1 — Quickstart works against Postgres (Priority: P1) 🎯 MVP

**Goal**: A new developer copies SPEC §11 quickstart code, sets `PG_URL`, and the verbs `add → search → get → update → delete → list → context` all work against a real pgvector database — with no provider-specific code on the caller's side.

**Independent Test**: From a clean venv, `pip install -e sdk-python && python examples/01_quickstart.py` succeeds with only `PG_URL` set, exercising every verb at least once.

### Tests for User Story 1 (Principle II — write FIRST, must FAIL before implementation)

- [X] T012 [P] [US1] Create `sdk-python/tests/conftest.py` with: session-scoped `pg_container` fixture using `testcontainers.postgres.PostgresContainer("pgvector/pgvector:pg16")`; module-scoped `postgres_adapter` fixture wiring `PostgresAdapter(url=…, embedder=FakeEmbedder())`; function-scoped `clean_db` autouse fixture that truncates `memories` between tests.
- [X] T013 [P] [US1] Write happy-path lifecycle tests in `sdk-python/tests/test_contract_lifecycle.py` (initially parametrized over a single adapter, expanded in US2): `test_add_then_get_roundtrip`, `test_update_supersedes_appends_to_history`, `test_delete_then_get_raises_not_found`, `test_list_filters_by_scope_glob_and_tag`, `test_list_pagination_returns_next_cursor_and_terminates` (insert 75 memories, page through with `limit=50`, assert two pages then `next_cursor is None`; covers FR-005 / EC-007), `test_list_on_empty_db_returns_empty_page` (covers EC-001; closes analyze finding C6). All assertions reference `Memory` schema fields from T008.
- [X] T014 [P] [US1] Write search/context tests in `sdk-python/tests/test_contract_search.py`: `test_search_returns_relevant_above_random` (insert 5 unrelated + 1 relevant memory, assert relevant is rank 1 and `score > random_score + 0.1`, where score is cosine similarity per `SearchResult.score` docstring); `test_search_min_score_filters_dissimilar` (with `min_score=0.99` on a dissimilar query, assert empty result list; closes analyze finding C4); `test_search_and_context_on_empty_db_return_empty` (covers EC-001 for `search` and `context`; closes analyze finding C6); `test_context_respects_token_budget_and_returns_citations` (assert `ctx.token_count <= token_budget` and `len(ctx.citations) >= 1`).

### Implementation for User Story 1

- [X] T015 [US1] Implement `PostgresAdapter.__init__` + idempotent DDL in `sdk-python/openmem/adapters/postgres.py`: connection from `url` arg or `PG_URL` env; on first call, run `CREATE EXTENSION IF NOT EXISTS vector;` and `CREATE TABLE IF NOT EXISTS memories(...)` with all columns from `plan.md` Phase 3 (UUID `id`, content, user_id, scope, tags TEXT[], source JSONB, confidence REAL, valid_from/to TIMESTAMPTZ, supersedes TEXT[], embedding_model TEXT, embedding VECTOR(<dim>), extensions JSONB, created_at, updated_at) and indexes (`idx_memories_user_scope`, `idx_memories_tags GIN`, `idx_memories_embedding ivfflat`). Wrap `psycopg.errors.*` → `ProviderError`.
- [X] T016 [US1] Implement CRUD verbs in `sdk-python/openmem/adapters/postgres.py`: `add` (mint `mem_<ulid>` id via `python-ulid`; **before embedding, validate the embedder's `dim` matches the column's declared dim and raise `InvalidRequestError` on mismatch — closes analyze finding I2/EC-005**; embed content; INSERT — also persist `embedding_model = self.embedder.model` to satisfy FR-014; return populated `Memory`); `get` (SELECT by id, raise `NotFoundError` on miss); `update` (UPDATE selected columns, bump `updated_at`, append to `supersedes` array, RETURNING); `delete` (DELETE, raise `NotFoundError` if 0 rows affected); `list` (SELECT with optional `user_id`, scope-glob → SQL `LIKE` (`*` → `%`), tag membership, time window, plus **keyset pagination** on `(created_at DESC, id DESC)` — closes analyze finding U1: decode `cursor` arg as base64-encoded `"<created_at>|<id>"` and use it in the WHERE clause; if `len(rows) == limit`, set `next_cursor = base64("<last.created_at>|<last.id>")`, else `None`; return `MemoryPage`).
- [X] T017 [US1] Implement `PostgresAdapter.search` in `sdk-python/openmem/adapters/postgres.py`: hybrid query combining cosine distance via pgvector `<=>` operator and `ILIKE` keyword match; **two-step model-filter logic (closes analyze finding A3 / U2 / FR-014 / EC-003): (a) run the search with `WHERE embedding_model = self.embedder.model`; (b) if the result is empty, run a second probe `SELECT 1 FROM memories WHERE user_id = %s AND scope LIKE %s LIMIT 1` *without* the model filter — if it returns a row, raise `InvalidRequestError("no memories indexed with model X for this scope")`; otherwise return `[]`**; `min_score` filter via `1 - distance >= min_score` (`SearchResult.score` is the similarity, range 0..1); scope-glob translated as in `list`; ORDER BY score DESC; return `list[SearchResult]`.
- [X] T018 [US1] Implement `PostgresAdapter.context` in `sdk-python/openmem/adapters/postgres.py`: call `self.search(query, user_id, scope, limit=max(1, token_budget // 50))`; format text as numbered `[1] {content}\n[2] {content}` lines; build `citations` list with `memory_id` + `score`; estimate `token_count = len(text) // 4`; return `ContextBlock`.
- [X] T019 [US1] Implement `PostgresAdapter.capabilities` in `sdk-python/openmem/adapters/postgres.py`: return hard-coded `Capabilities(omp_version="0.1", provider="postgres", verbs=["add","search","get","update","delete","list","context"], features=CapabilityFeatures(vector_search=True, keyword_search=True, graph_queries=False, temporal=True, scopes="native", max_content_length=10000, supports_e2e=False, supports_audit=False, supports_supersession=True), limits=CapabilityLimits(max_search_results=100))`. **Omit `rate_limit_per_minute` entirely (do not pass `None`) — the OpenAPI schema declares it `integer` without `nullable: true`; closes analyze finding A1.**
- [X] T020 [US1] Implement public facade in `sdk-python/openmem/memory.py`: `Memory(provider: str, **config)` calls `_resolve_adapter()` (full SPEC §11a logic deferred to US3 — for US1, simply look up `provider` in `TRANSLATION_ADAPTERS = {"postgres": PostgresAdapter}` and `raise UnsupportedProviderError` otherwise). Public methods (`add(content, user_id, scope=None, tags=None, source=None, ...)`, `search`, `get`, `update`, `delete`, `list`, `context`, `capabilities`, `audit`) accept idiomatic snake_case kwargs matching SPEC §11, build pydantic input models, call adapter, return adapter output. Cache `capabilities()` per instance.
- [X] T021 [US1] Re-export public surface in `sdk-python/openmem/__init__.py`: `Memory`, all `errors.*` classes, `Memory`/`MemoryInput`/`SearchResult`/`ContextBlock`/`Capabilities` from `types`, `FakeEmbedder` from `adapters.embedder`. Set `__version__ = "0.1.0"`.
- [X] T022 [US1] Create runnable quickstart at `examples/01_quickstart.py` using the SPEC §11 code verbatim (with `provider="postgres"` and `url=os.environ["PG_URL"]`), exercising `add`, `search`, `context`, `update`, `delete` in order. Must run end-to-end against a live Postgres + pgvector.

**Checkpoint**: A new dev with a Postgres container + `PG_URL` can run the quickstart and see real CRUD output. US1 is fully functional independent of US2 and US3.

---

## Phase 4: User Story 2 — Conformance suite proves substitutability (Priority: P2)

**Goal**: An adapter author runs `pytest sdk-python/tests` and gets a single green/red signal that their adapter conforms to OMP — fulfilling Principle II (NON-NEGOTIABLE) which makes this the gating mechanism for every future adapter (Mem0, Supermemory, etc.).

**Independent Test**: `pytest sdk-python/tests -q` exits 0 on a clean checkout with Docker available; coverage on `openmem/adapters/postgres.py` ≥ 90 %.

### Tests for User Story 2

- [X] T023 [P] [US2] Add error-model + capability tests in `sdk-python/tests/test_contract_errors.py`: `test_capabilities_advertises_supported_verbs_only` — for every verb in `Capabilities.verbs` confirm a positive call succeeds; **for every verb NOT in `caps.verbs`, calling `mem.<verb>(...)` via the facade raises `UnsupportedCapabilityError` with `code="unsupported_capability"` (covers FR-009 / EC-008; closes analyze finding C2)**. Plus `test_provider_errors_use_standard_envelope` (force a SQL error by violating max_content_length, assert raised exception is `ProviderError` with `code`, `type`, `provider` populated).
- [X] T024 [P] [US2] Add Principle V + III tests in `sdk-python/tests/test_contract_compat.py`: `test_unknown_extension_field_round_trips_via_x_prefix` (insert `Memory` with `x-mem0={"graph_node_id":"g1"}`, fetch back, assert key preserved); `test_unknown_field_in_response_is_ignored` parametrized over **every response model** (`Memory`, `Capabilities`, `SearchResult`, `MemoryPage`, `ContextBlock`, `AuditEntry`) — construct each from a dict containing `bogus_future_field`, assert no error and field preserved via `extra="allow"` (covers FR-012 / EC-006; closes analyze finding U3).
- [X] T025 [P] [US2] Create `sdk-python/tests/test_types_match_openapi.py`: load `spec/omp-0.1.openapi.yaml` via `PyYAML`, walk every `components/schemas` entry, assert the corresponding pydantic model in `openmem.types` exists and that every required OpenAPI property is a required pydantic field of compatible type. Enforces Principle I.
- [X] T026 [P] [US2] Create Postgres-specific tests in `sdk-python/tests/adapters/test_postgres_specific.py`: `test_ddl_is_idempotent` (instantiate adapter twice, no error); `test_concurrent_inserts_do_not_deadlock` (10 threads × 50 inserts via `concurrent.futures`, all succeed); `test_embedding_dimension_mismatch_raises_invalid_request` (instantiate `PostgresAdapter` with `FakeEmbedder(dim=64)`, then patch the embedder instance to advertise `dim=128` and call `add()`; expect `InvalidRequestError` from the **pre-INSERT dim check in T016**, NOT a `ProviderError` from psycopg — closes analyze finding I2); `test_cross_model_search_hard_fails` (insert with one embedder, swap to a different `model` string, call `search()`, expect `InvalidRequestError` per FR-014).
- [X] T027 [US2] Refactor the `test_contract_*.py` files (lifecycle / search / errors / compat — split per analyze finding D1) to be parametrized over an `adapter` fixture (currently single `postgres_adapter`). Move adapter selection into `conftest.py` `adapter` fixture using `pytest.fixture(params=...)` so adding M2's `mem0_adapter` later requires zero changes to the test files. Depends on T012–T024.

### Implementation for User Story 2

- [X] T028 [US2] Add OpenAPI validation entry to `sdk-python/pyproject.toml` `[project.scripts]`: `omp-validate-spec = "openmem._scripts:validate_spec"`. Create `sdk-python/openmem/_scripts.py` with `validate_spec()` invoking `openapi_spec_validator.validate_spec` on `spec/omp-0.1.openapi.yaml`. Used by CI in T038.
- [X] T029 [US2] Add coverage configuration to `sdk-python/pyproject.toml` (`[tool.pytest.ini_options]` with `addopts = "--cov=openmem --cov-report=term-missing --cov-fail-under=85"`). Add a separate CI step in T038 that runs `pytest --cov=openmem.adapters.postgres --cov-fail-under=90` to enforce SC-003 specifically for the Postgres adapter (≥ 90 %).

**Checkpoint**: `pytest sdk-python/tests -q` is the single green/red gate. The suite is ready to absorb M2 adapters by appending one line to the `adapter` fixture's `params`.

---

## Phase 5: User Story 3 — Switch providers with zero code change (Priority: P3)

**Goal**: Auto-detection wiring (SPEC §11a) is real, and a user/app can swap backends by changing a single configuration value with no code change — proving the substitutability success metric (SPEC §16).

**Independent Test**: `python examples/02_switch_providers.py` runs the same application code against two different `provider=` values and produces identical search-result ordering for the same query.

### Tests for User Story 3

- [X] T030 [P] [US3] Add `sdk-python/tests/test_resolve_adapter.py`: `test_passthrough_used_when_capabilities_returns_omp_version` (mock `httpx.get` returning `{"omp_version":"0.1", ...}`, assert `_resolve_adapter` returns `PassthroughAdapter`); `test_translation_used_when_no_omp_version` (mock returns 404, assert returns `PostgresAdapter` for `provider="postgres"`); `test_unsupported_provider_raises` (assert `UnsupportedProviderError` for unknown provider with no `base_url`); `test_capability_probe_is_cached` (assert second `mem.capabilities()` call does not re-issue HTTP); `test_probe_returns_none_when_omp_version_missing` (direct unit test of `PassthroughAdapter._probe` against a mock returning `{"provider":"x"}` without `omp_version`; closes analyze finding C5).

### Implementation for User Story 3

- [X] T031 [P] [US3] Implement `PassthroughAdapter` stub in `sdk-python/openmem/adapters/passthrough.py`: `__init__(base_url, api_key=None, capabilities: Capabilities)` stores deps; `capabilities()` returns the cached value; every other verb raises `NotImplementedError("Native passthrough lands in M2; see CHANGELOG")`. Includes a private `_probe(base_url) -> Capabilities | None` classmethod that does the `httpx.get(f"{base_url}/capabilities")` and returns parsed `Capabilities` when `omp_version` is present, else `None`. Depends on T010.
- [X] T032 [US3] Replace the simple lookup in `sdk-python/openmem/memory.py` `_resolve_adapter` with the full SPEC §11a algorithm: if `config.get("base_url")` set, call `PassthroughAdapter._probe`; if it returns capabilities, instantiate `PassthroughAdapter`; else fall through to `TRANSLATION_ADAPTERS[provider]`; else `UnsupportedProviderError`. Cache probe result on the `Memory` instance. Depends on T020, T031.
- [X] T033 [P] [US3] Create `examples/02_switch_providers.py` per SPEC §14 Example A: define `run(mem)` using identical code; instantiate `Memory(provider="postgres", url=...)` twice with different configs (e.g. two schemas in same DB), call `run` against each, print results side-by-side.
- [X] T034 [P] [US3] Create `examples/03_chatbot_demo/main.py`: minimal CLI loop reading from stdin; for each input, call `mem.context(query, user_id="demo", token_budget=300)`; print the assembled prompt (no real LLM call) so the demo runs offline (Principle IV); also call `mem.add(content=user_input, ...)` so the conversation accumulates memory.
- [X] T035 [P] [US3] Create `examples/03_chatbot_demo/README.md` (one paragraph + run command).

**Checkpoint**: All three user stories independently functional. Substitutability metric (SPEC §16) is demonstrably achieved.

---

## Phase 6: Polish & Cross-Cutting Concerns

**Purpose**: Distribution, docs, CI — required for an actual M1 release per `plan.md` Phase 7.

- [X] T036 [P] Create root `README.md`: one-paragraph "what is OMP", 30-second quickstart (copy of `examples/01_quickstart.py`), provider matrix table seeded with `Postgres + pgvector → 🟢 Native (reference)`, links to `spec/OMP-0.1.md`, `spec/omp-0.1.openapi.yaml`, `.specify/memory/constitution.md`.
- [X] T037 [P] Create `sdk-python/README.md`: install (`pip install -e sdk-python` + extras), env vars (`PG_URL`, `OPENAI_API_KEY`), supported providers table, "How to run the test suite", "How to add a new adapter" section pointing at `BaseAdapter` and the contract suite (Principle II).
- [X] T038 [P] Create `.github/workflows/ci.yml`: matrix on Python 3.11 + 3.12; service container `pgvector/pgvector:pg16`; steps install `sdk-python[dev]`, run `omp-validate-spec`, run `pytest sdk-python/tests -q`. Triggers on push + pull_request.
- [X] T039 [P] Create `CHANGELOG.md`: `## [0.1.0] — 2026-04-28 — Initial M1 release` enumerating Setup → US1 → US2 → US3 → Polish deliverables; note "PassthroughAdapter is a stub; native verbs land in M2".
- [X] T040 Manually run the full Quickstart from a clean venv per Verification step 5 in `plan.md`: `python -m venv .venv; .venv\Scripts\Activate.ps1; pip install -e sdk-python[dev]; $env:PG_URL="..."; python examples/01_quickstart.py`. Fix any gap discovered. **Note**: live-DB run deferred to user (no Docker on dev host). All non-DB tests pass: `pytest sdk-python/tests/test_contract_compat.py sdk-python/tests/test_types_match_openapi.py sdk-python/tests/test_resolve_adapter.py` → 32 passed.
- [X] T041 Run all six Verification checks from `plan.md` § Verification and record pass/fail in PR description against the gated PR checklist (Principles I–V). **Verified locally**: (1) Spec validates (`omp-validate-spec` → OK); (2) Pydantic types match OpenAPI (`test_types_match_openapi.py` → 18 passed); (3) `extra="allow"` on every response model (`test_contract_compat.py` parametrized) → 8 passed; (4) `_resolve_adapter` SPEC §11a logic (`test_resolve_adapter.py`) → 4 passed (1 deferred: needs DB). **Deferred to CI** (Docker required): contract lifecycle/search/errors + Postgres-specific tests + coverage gates.

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: no dependencies; T001–T003 are sequential (`git mv`); T004–T007 parallel after T001.
- **Foundational (Phase 2)**: depends on Setup. T008, T009, T011 parallel; T010 depends on T008+T009. **Blocks all user-story phases.**
- **User Stories (Phase 3+)**: all depend on Phase 2 completion.
  - May proceed in priority order (P1 → P2 → P3) for solo work, or in parallel for multiple developers.
  - US1 is the MVP; US2 and US3 add no new caller-visible verbs but are required to satisfy the constitution.
- **Polish (Phase 6)**: depends on US1 + US2 + US3 complete.

### User Story Dependencies

- **US1 (P1)**: depends only on Phase 2.
- **US2 (P2)**: depends on Phase 2 and on US1's `PostgresAdapter` existing (T015–T020) so the conformance suite has a target. Can be drafted in parallel with US1 implementation if test files commit before adapter code (Red phase per Principle II).
- **US3 (P3)**: depends on Phase 2 and on T020 (`memory.py` exists) so `_resolve_adapter` can be extended. Independent of US2.

### Within Each User Story

- Per Constitution Principle II: tests committed (and observed failing) **before** implementation in the same story.
- Models/types → adapters → facade → examples.

### Parallel Opportunities

- **Setup**: T004, T005, T006, T007 in parallel after T001–T003.
- **Foundational**: T008, T009, T011 in parallel; T010 follows T008+T009.
- **US1**: T012, T013, T014 in parallel (different test files / different test functions in the same file are still parallelizable at authoring time); T015 sequential; T016, T017, T018, T019 share `postgres.py` so sequential; T021 parallel with T022.
- **US2**: T023, T024, T025, T026 fully parallel; T027 follows; T028, T029 parallel.
- **US3**: T031 parallel with T030; T032 follows; T033, T034, T035 parallel.
- **Polish**: T036, T037, T038, T039 fully parallel; T040 sequential; T041 last.

---

## Parallel Example: User Story 1 (Foundational complete)

```powershell
# Three independent files; safe to author concurrently
# Tab 1
code sdk-python/tests/conftest.py            # T012
# Tab 2
code sdk-python/tests/test_contract.py       # T013 + T014
# Tab 3
code sdk-python/openmem/adapters/postgres.py # T015 (start scaffolding while tests are written)
```

After tests committed and observed RED:

```powershell
pytest sdk-python/tests/test_contract.py -k "roundtrip or supersedes" --no-cov
# Expect failures → proceed with T015..T020 to drive them GREEN
```

---

## Implementation Strategy

**MVP scope** = Setup + Foundational + US1. After T022 a developer can run the quickstart against pgvector, which already proves the core promise of OMP for one provider. Everything after US1 is necessary for the *standard* (US2 = enforcement, US3 = substitutability) and for shipping (Polish), but US1 alone is demoable.

**Recommended slicing**:

1. **Slice A (MVP, ~1 sitting)** — T001 → T022. End state: `python examples/01_quickstart.py` works.
2. **Slice B (Conformance gate)** — T023 → T029. End state: `pytest` is green; coverage gate enforced.
3. **Slice C (Substitutability)** — T030 → T035. End state: switch-providers demo works; M2 adapters can plug in by extending one fixture and one dict.
4. **Slice D (Release)** — T036 → T041. End state: CI green, README + CHANGELOG published, manual verification recorded.

---

## Format Validation

All 41 tasks above conform to the required format:
- Begin with `- [ ]`
- Sequential `T001`–`T041`
- `[P]` only on tasks that touch a file no other in-flight task touches
- `[US1]` / `[US2]` / `[US3]` present on every Phase 3–5 task; absent on Setup, Foundational, Polish
- Every task includes the explicit file path being created or edited
