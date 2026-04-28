# Plan: M1 — Python SDK skeleton + Postgres adapter (end-to-end)

**Branch**: `001-m1-python-sdk-postgres` | **Date**: 2026-04-28
**Constitution**: [.specify/memory/constitution.md](../../.specify/memory/constitution.md) (v1.0.0)
**Spec source**: [omp-0.1.openapi.yaml](../../omp-0.1.openapi.yaml), [SPEC_Version8.md](../../SPEC_Version8.md)

## Summary

Deliver a `pip install`-able Python SDK whose `Memory` class implements every
verb in the OpenAPI spec, backed by a working **Postgres + pgvector**
reference adapter, gated by a parametrized contract suite, demonstrated by
runnable examples, documented in a quickstart README. Satisfies Principles
I (spec-first), II (contract-tested), III (forward-compat), IV (zero-vendor
reference path), V (`x-*` extensions). Sets the harness future M2/M3
adapters plug into.

**Approach.** Pydantic v2 types mirror the OpenAPI components; thin `Memory`
facade dispatches to a `BaseAdapter` ABC; Postgres adapter uses a pluggable
`Embedder` (OpenAI in prod, deterministic `FakeEmbedder` in tests so the
suite runs offline); `PassthroughAdapter` stub exists so SPEC §11a
auto-detection wiring is real, not vapor. No HTTP server in M1.

## Phases

Each phase is independently verifiable.

### Phase 0 — Repo skeleton & spec relocation *(blocks all later phases)*

1. Create directory tree from SPEC §13:
   `spec/`, `sdk-python/openmem/{adapters/}`, `sdk-python/tests/{adapters/}`,
   `examples/`, plus root `README.md` and `CHANGELOG.md`.
2. Move existing files into `spec/`:
   - `omp-0.1.openapi.yaml` → `spec/omp-0.1.openapi.yaml`
   - `SPEC_Version8.md` → `spec/OMP-0.1.md`
3. Add `sdk-python/pyproject.toml` (PEP 621, package `openmem`, Python
   `>=3.11`, deps `pydantic>=2`, `httpx`, `psycopg[binary]>=3.1`,
   `pgvector>=0.2`; extras `openai`, `dev`).
4. Add `.gitignore` entries: `__pycache__/`, `.venv/`, `dist/`,
   `*.egg-info/`, `.pytest_cache/`.
5. Update [.specify/templates/plan-template.md](../../.specify/templates/plan-template.md)
   Constitution Check to enumerate Principles I–V (closes ⚠ pending item
   in constitution Sync Impact Report).

### Phase 1 — Types & errors *(parallel with Phase 2 once Phase 0 done)*

6. `sdk-python/openmem/types.py` — pydantic v2 models matching OpenAPI
   `components/schemas` exactly: `Memory`, `MemoryInput`, `MemoryUpdate`,
   `MemorySource`, `MemoryPage`, `SearchResult`, `ContextBlock`,
   `Capabilities`, `CapabilityFeatures`, `CapabilityLimits`, `AuditEntry`.
   Required: `id`, `content`, `user_id`, `created_at`. `extra="allow"` on
   `Memory` for `x-<provider>` round-trip (Principle V).
7. `sdk-python/openmem/errors.py` — `OMPError` base + subclasses per
   enumerated `code` (`UnauthorizedError`, `ScopeDeniedError`,
   `NotFoundError`, `InvalidRequestError`, `RateLimitedError`,
   `UnsupportedCapabilityError`, `ProviderError`). Each carries `code`,
   `type`, `provider`, `request_id`, `message`. `from_response_dict()`
   classmethod for adapters.

### Phase 2 — Adapter framework *(parallel with Phase 1)*

8. `sdk-python/openmem/adapters/base.py` — `BaseAdapter(ABC)` with
   abstract methods exactly matching SPEC §12: `add`, `search`, `get`,
   `update`, `delete`, `list`, `context`, `capabilities`, optional
   `audit`. Signatures use Phase 1 types.
9. `sdk-python/openmem/adapters/embedder.py` — `Embedder` protocol with
   `embed(texts: list[str]) -> list[list[float]]` and `dim: int`. Two
   implementations: `OpenAIEmbedder` (text-embedding-3-small, dim=1536,
   imported lazily) and `FakeEmbedder` (deterministic hash-based vectors,
   dim=64, used by tests + offline demo — Principle IV).
10. `sdk-python/openmem/adapters/passthrough.py` — stub `PassthroughAdapter`
    that probes `/capabilities` via `httpx`; verbs raise
    `NotImplementedError("native passthrough lands in M2")`. Exists so
    auto-detection wiring is real.

### Phase 3 — Postgres + pgvector adapter *(depends on Phase 1+2)*

11. `sdk-python/openmem/adapters/postgres.py`:
    - Connection via `psycopg.Connection` from config string.
    - First-use idempotent DDL: `CREATE EXTENSION IF NOT EXISTS vector;`
      + `memories` table mirroring `Memory` schema (UUID `id`,
      `content TEXT`, `user_id TEXT`, `scope TEXT`, `tags TEXT[]`,
      `source JSONB`, `confidence REAL`, `valid_from TIMESTAMPTZ`,
      `valid_to TIMESTAMPTZ`, `supersedes TEXT[]`, `embedding_model TEXT`,
      `embedding VECTOR(<dim>)`, `extensions JSONB` for `x-*`,
      `created_at`, `updated_at`) + indexes on `(user_id, scope)`,
      `tags GIN`, `embedding ivfflat`.
    - `add()` — generate `mem_<ulid>` id, embed content, INSERT.
    - `search()` — hybrid: cosine `<=>` + ILIKE keyword; `min_score`
      filter; scope glob `*` → SQL `LIKE`.
    - `get`/`update`/`delete`/`list` — straightforward SQL; `update`
      bumps `updated_at` and appends to `supersedes`.
    - `context()` — calls `search()` with `limit = token_budget // 50`,
      formats numbered `[1] content` lines, returns `ContextBlock(text,
      citations, token_count)` with `len(text)//4` token estimate.
    - `capabilities()` — hard-coded `Capabilities(provider="postgres",
      omp_version="0.1", verbs=[…all except audit], vector_search=True,
      keyword_search=True, temporal=True, scopes="native",
      supports_supersession=True, supports_audit=False,
      max_content_length=10000)`.
    - Errors: catch `psycopg.errors.*` → `ProviderError`; missing rows
      → `NotFoundError`.

### Phase 4 — Memory facade & auto-detection *(depends on Phase 3)*

12. `sdk-python/openmem/memory.py` — public `Memory`:
    - `__init__(provider, **config)` dispatches via `_resolve_adapter()`
      (SPEC §11a): if config has `base_url`, probe `/capabilities` and
      use `PassthroughAdapter` when `omp_version` present; else look up
      `TRANSLATION_ADAPTERS = {"postgres": PostgresAdapter}`; else raise
      `UnsupportedProviderError`.
    - Public methods (`add`, `search`, `get`, `update`, `delete`, `list`,
      `context`, `capabilities`, `audit`) accept idiomatic snake_case
      kwargs matching SPEC §11, build pydantic inputs, call adapter,
      return pydantic outputs.
    - Cache capability probe per `Memory` instance.
13. `sdk-python/openmem/__init__.py` — re-export `Memory`, `errors`,
    selected types.

### Phase 5 — Conformance test suite *(test-first per Principle II)*

14. `sdk-python/tests/conftest.py`:
    - `pg_container` (session-scoped) — `testcontainers.postgres.PostgresContainer`
      with `pgvector/pgvector:pg16` image.
    - `adapter` — parametrized fixture yielding each registered adapter
      configured against the container with `FakeEmbedder`. M1 yields
      only `PostgresAdapter`; M2 appends without changing the test file.
15. `sdk-python/tests/test_contract.py` — single parametrized suite:
    - `test_add_then_get_roundtrip`
    - `test_search_returns_relevant_above_random`
    - `test_list_filters_by_scope_glob_and_tag`
    - `test_update_supersedes_appends_to_history`
    - `test_delete_then_get_raises_not_found` (Principle III error model)
    - `test_context_respects_token_budget_and_returns_citations`
    - `test_capabilities_advertises_supported_verbs_only`
    - `test_unknown_extension_field_round_trips_via_x_prefix` (Principle V)
    - `test_unknown_field_in_response_is_ignored` (forward-compat,
      Principle III)
16. `sdk-python/tests/adapters/test_postgres_specific.py` — pgvector DDL
    runs idempotently; concurrent inserts don't deadlock; embedding
    dimension mismatch raises `InvalidRequestError`.
17. `sdk-python/tests/test_types_match_openapi.py` — load YAML, assert
    every `components/schemas` field exists on the matching pydantic
    model with same required-ness (Principle I).

### Phase 6 — Examples *(depends on Phase 4; parallel with Phase 7)*

18. `examples/01_quickstart.py` — verbatim from SPEC §11, `PG_URL` env.
19. `examples/02_switch_providers.py` — same code, `provider="postgres"`
    twice with different configs, demonstrating zero-code-change
    substitutability (SPEC §14 Example A).
20. `examples/03_chatbot_demo/main.py` — minimal CLI: prompt →
    `mem.context` → echo assembled prompt. Stub LLM (no API call) so it
    runs offline (Principle IV).

### Phase 7 — Docs & wiring *(parallel with Phase 6)*

21. Root `README.md` — what OMP is (1 paragraph), 30-second quickstart
    (copy of `examples/01_quickstart.py`), provider matrix table seeded
    with Postgres = 🟢 Native (reference), links to spec + constitution.
22. `sdk-python/README.md` — install, env vars, supported providers,
    how to run `pytest`, how to add a new adapter (pointer to
    `BaseAdapter` + the contract suite, per Principle II).
23. `CHANGELOG.md` — `## [0.1.0] — 2026-04-28 — Initial M1 release`
    enumerating Phase 0–7 deliverables.
24. `.github/workflows/ci.yml` — GitHub Actions: matrix on Python
    3.11/3.12, spin up pgvector container, run
    `pytest sdk-python/tests` and
    `python -m openapi_spec_validator spec/omp-0.1.openapi.yaml`.

## Relevant Files

- `spec/omp-0.1.openapi.yaml` (moved from root) — drives every type +
  adapter; Principle I enforcement target.
- `spec/OMP-0.1.md` (moved + renamed from `SPEC_Version8.md`) —
  referenced from READMEs.
- `sdk-python/openmem/memory.py` — public facade; mirrors SPEC §11
  examples line-for-line.
- `sdk-python/openmem/adapters/base.py` — `BaseAdapter` ABC from
  SPEC §12.
- `sdk-python/openmem/adapters/postgres.py` — reference Native adapter;
  the Principle IV proof-of-life.
- `sdk-python/openmem/adapters/embedder.py` — `Embedder` protocol +
  `FakeEmbedder` + lazy `OpenAIEmbedder`.
- `sdk-python/openmem/adapters/passthrough.py` — stub so M2 wiring
  exists.
- `sdk-python/openmem/types.py`, `errors.py` — handwritten from OpenAPI.
- `sdk-python/tests/test_contract.py` — parametrized suite gating *all*
  future adapters (Principle II).
- `sdk-python/tests/conftest.py` — pgvector testcontainer fixture.
- `examples/0[1-3]_*.py` — runnable proofs of SPEC §14 use cases A–D.
- `README.md`, `CHANGELOG.md`, `.github/workflows/ci.yml`.
- [.specify/templates/plan-template.md](../../.specify/templates/plan-template.md)
  — small edit to enumerate Principles I–V in Constitution Check.

## Verification

1. **Spec validity (Principle I).** `python -m openapi_spec_validator
   spec/omp-0.1.openapi.yaml` exits 0 in CI.
2. **Type ↔ schema parity (Principle I).**
   `test_types_match_openapi` passes.
3. **Conformance suite green (Principle II).** `pytest sdk-python/tests
   -q` passes locally and in CI; coverage ≥ 90 % on
   `openmem/adapters/postgres.py`.
4. **Substitutability E2E (Principle IV; SPEC §16).**
   `python examples/02_switch_providers.py` produces identical search
   output across the two configured providers.
5. **Quickstart from clean venv.**
   `python -m venv .venv && pip install -e sdk-python &&
   python examples/01_quickstart.py` succeeds with only `PG_URL` set.
6. **Constitution gate manual review.** PR description checks all 5
   boxes from the gated PR checklist (Principles I–V) before merge.
7. **Forward-compat (Principle III).**
   `test_unknown_field_in_response_is_ignored` passes.

## Constitution Check (Principles I–V)

| Principle | How this plan satisfies it |
|---|---|
| I. Spec-first | Pydantic types validated against OpenAPI by `test_types_match_openapi`; CI runs `openapi-spec-validator`. |
| II. Contract-tested | Single parametrized `test_contract.py`; written before adapter implementation per Red→Green→Refactor. |
| III. Backward/forward compat | `extra="allow"` on `Memory`; explicit unknown-field test; required fields enforced by pydantic. |
| IV. Provider neutrality | Postgres + pgvector reference adapter is the default; `FakeEmbedder` keeps tests + demo runnable with zero accounts. |
| V. Open extensibility | `extensions JSONB` column round-trips `x-<provider>` fields; covered by an explicit test. |

No violations → no Complexity Tracking entries.

## Decisions

- **Single-package layout** (`sdk-python/openmem`) with extras
  (`pip install openmem[postgres]`) rather than a monorepo of adapter
  packages.
- **`testcontainers` for the suite** — not an in-memory fake; faking
  vector math would violate Principle II in spirit.
- **`FakeEmbedder` ships in the package** (not test-only) so the
  offline chatbot demo and community adapters can reuse it.
- **No HTTP server in M1.** A FastAPI passthrough server is deferred
  to M2/M3 when Mem0/Supermemory adapters create real demand.
- **Excluded from M1:** TypeScript SDK (M3), Mem0/Supermemory adapters
  (M2), OAuth flows, audit-log persistence (advertised
  `supports_audit=false`), graph queries, E2E encryption.

## Further Considerations

1. **Cross-embedding-model search** (spec open question §17.4).
   Option A: hard-fail when query model ≠ stored model / Option B:
   warn and re-embed / Option C: ignore. **Recommended: A** for M1.
2. **ID format.** Spec shows `mem_abc123`. Option A: ULID prefixed
   `mem_` (sortable, one tiny dep `python-ulid`) / Option B: UUIDv7 /
   Option C: provider-defined. **Recommended: A.**
3. **Async vs. sync API.** Spec examples are sync. Option A: sync-only
   in M1 / Option B: dual / Option C: async-only. **Recommended: A** —
   add `AsyncMemory` alongside the TS SDK in M3.
