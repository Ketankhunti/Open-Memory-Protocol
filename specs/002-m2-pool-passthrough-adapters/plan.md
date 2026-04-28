# Implementation Plan: M2 — Connection pooling, native passthrough, first translation adapters

**Branch**: `002-m2-pool-passthrough-adapters` | **Date**: 2026-04-28 | **Spec**: [spec.md](spec.md)
**Constitution**: [.specify/memory/constitution.md](../../.specify/memory/constitution.md) (v1.0.2)

## Summary

Three orthogonal improvements to the M1 SDK, all gated by the existing parametrized contract suite (Constitution Principle II):

1. **Pooling** — replace M1's `@_synchronized` `RLock` stop-gap in `PostgresAdapter` with `psycopg_pool.ConnectionPool`; concurrent throughput rises ≥5× and the 5-minute concurrency hang from M1 is eliminated.
2. **Native passthrough** — `PassthroughAdapter` implements every OMP verb over HTTP per the OpenAPI spec, turning any conformant server into a drop-in `Memory(base_url=...)` backend.
3. **Translation adapters** — first three real third-party adapters (Mem0, Supermemory, Letta), each plugged into the contract suite via one fixture entry per Principle II.

Plus tooling: `pytest-timeout` in `[dev]` extras with a 30s default per-test timeout; `examples/02_switch_providers.py` upgraded to demonstrate substitutability across two genuinely different providers.

## Technical Context

**Language/Version**: Python 3.11+ (matches M1 `pyproject.toml`)
**Primary Dependencies**: pydantic v2, httpx, psycopg[binary]≥3.1, **psycopg-pool≥3.2** (new), pgvector≥0.2; new optional extras `mem0`, `supermemory`, `letta`
**Storage**: Postgres 16 + pgvector (unchanged); remote OMP services for passthrough; vendor SaaS for translation adapters
**Testing**: pytest, pytest-cov, **pytest-timeout** (new), testcontainers[postgres], httpx `MockTransport` for passthrough, recorded fixtures + opt-in live mode for translation adapters
**Target Platform**: Linux/macOS/Windows dev + CI; Python library, no server
**Project Type**: Single-project Python library (`sdk-python/openmem/`) — same layout as M1
**Performance Goals**: ≥5× concurrent `add()` throughput vs M1 baseline at `pool_size=10`, 200 worker threads (SC-001); concurrency test <30s (SC-002); full suite <5 min (SC-006)
**Constraints**: No new vendor coupling in the reference path (Principle IV); zero edits in `test_contract_*.py` files when adding adapters (Principle II / SC-005); pool default `min_size=1, max_size=10, timeout=30s`; per-test default timeout 30s
**Scale/Scope**: Five adapters in conformance matrix at end of M2 (`postgres`, `passthrough`, `mem0`, `supermemory`, `letta`); ~6 new modules; ~25 new tests

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-checked at the bottom of this document after Phase 1 design.*

| Principle | How this plan satisfies it |
|---|---|
| I. Spec-First, Single Source of Truth (NON-NEGOTIABLE) | `PassthroughAdapter` HTTP mapping is generated 1:1 from `spec/omp-0.1.openapi.yaml` `paths` (see [contracts/passthrough-http.md](contracts/passthrough-http.md)); no wire-only models — uses existing `openmem.types`. Translation adapters round-trip the same `Memory` schema; any field the spec adds in the future flows through `extra="allow"`. |
| II. Adapter Conformance via Shared Contract Tests (NON-NEGOTIABLE) | Pool, passthrough, and all three translation adapters extend the `adapter` fixture in `tests/conftest.py` and run the unmodified `test_contract_*.py` files. Failing the suite blocks merge. New adapters added by appending one `params` entry — zero edits in test bodies (FR-015, SC-005). |
| III. Backward and Forward Compatibility | All M1 verb signatures preserved. `PostgresAdapter.__init__` keeps positional + keyword compat (FR-002); pool args are kwargs with defaults. `RLock` removal is documented in `CHANGELOG.md` (EC-009); no spec field/verb is removed. Unknown response fields tolerated via `extra="allow"`. |
| IV. Provider Neutrality and User Sovereignty | Postgres + pgvector remains the first-class reference path (`02_switch_providers.py` keeps `postgres` as one of its two demos). New translation extras are *opt-in* (`pip install openmem[mem0]` etc.); none required to import or use the SDK. No telemetry; api keys never logged (FR-011). |
| V. Open Extensibility via Namespaced Fields | Every translation adapter routes provider-specific fields through `x-mem0` / `x-supermemory` / `x-letta` extensions on the `Memory` model — already supported by `extra="allow"`. Standard fields are never overridden by extension data. |

**Constitution gate result**: ✅ Pass. No violations; **Complexity Tracking** table at the bottom is empty.

## Project Structure

### Documentation (this feature)

```text
specs/002-m2-pool-passthrough-adapters/
├── plan.md                       # This file (/speckit.plan)
├── research.md                   # Phase 0 — pool/HTTP/translation decisions
├── data-model.md                 # Phase 1 — pool config + adapter input/output mappings
├── quickstart.md                 # Phase 1 — install/run for each new adapter
├── contracts/
│   ├── passthrough-http.md       # OMP verb ↔ HTTP method/path/body/error table
│   ├── mem0-mapping.md           # OMP verb ↔ Mem0 SDK call mapping
│   ├── supermemory-mapping.md    # OMP verb ↔ Supermemory API mapping
│   └── letta-mapping.md          # OMP verb ↔ Letta SDK mapping
├── spec.md                       # (already exists)
├── checklists/
│   └── requirements.md           # (already exists)
└── tasks.md                      # /speckit.tasks output (NOT created here)
```

### Source Code (repository root)

```text
sdk-python/
├── openmem/
│   ├── adapters/
│   │   ├── postgres.py          # MODIFIED: pool replaces RLock; remove _synchronized
│   │   ├── passthrough.py       # MODIFIED: implement all verbs over httpx
│   │   ├── mem0.py              # NEW (US3)
│   │   ├── supermemory.py       # NEW (US3)
│   │   ├── letta.py             # NEW (US3)
│   │   ├── _http.py             # NEW: shared httpx client + error envelope decoder
│   │   ├── base.py              # unchanged
│   │   └── embedder.py          # unchanged
│   └── memory.py                # MODIFIED: register mem0/supermemory/letta in dispatch
├── tests/
│   ├── conftest.py              # MODIFIED: add 4 fixtures + 4 params entries; mock-mode by default
│   ├── adapters/
│   │   ├── test_postgres_pool.py        # NEW: pool exhaustion, recycle, concurrency
│   │   ├── test_passthrough_native.py   # NEW: per-verb HTTP behavior, error mapping
│   │   ├── test_mem0_mapping.py         # NEW: unit-level mapper tests with fixtures
│   │   ├── test_supermemory_mapping.py  # NEW
│   │   └── test_letta_mapping.py        # NEW
│   └── test_contract_*.py       # UNCHANGED (Principle II / SC-005)
└── pyproject.toml               # MODIFIED: psycopg-pool dep, pytest-timeout in [dev], extras

examples/
└── 02_switch_providers.py       # MODIFIED: postgres + (mem0 OR passthrough)

CHANGELOG.md                     # MODIFIED: M2 release notes incl. RLock removal
```

**Structure Decision**: Single-project Python library — same layout as M1. No new top-level dirs.

## Phases

### Phase 0 — Research (resolve assumptions) → produces [research.md](research.md)

Decisions consolidated:

1. **Pool implementation** — `psycopg_pool.ConnectionPool` (sync); rejected alternatives.
2. **HTTP client** — reuse `httpx.Client` (already a dep); `MockTransport` for tests.
3. **Translation adapter wire choice** — Mem0 Python SDK, Supermemory REST, Letta Python SDK; rationale per provider.
4. **Test isolation strategy** — recorded fixtures by default, live mode opt-in via env vars; CI runs mocked.
5. **Capability shape per adapter** — explicit table.

### Phase 1 — Design & Contracts → produces [data-model.md](data-model.md), [contracts/](contracts/), [quickstart.md](quickstart.md)

1. **Connection pool model** (`data-model.md`):
   - `PostgresAdapter` constructor surface adds `pool_min_size: int = 1`, `pool_max_size: int = 10`, `pool_timeout: float = 30.0`.
   - Internal: `self._pool: psycopg_pool.ConnectionPool`. Each verb does `with self._pool.connection() as conn: with conn.cursor() as cur: ...`.
   - `_synchronized` and `self._lock` deleted.
   - Pool exhaustion → `ProviderError("connection pool exhausted")`; broken connection → pool auto-recycles, verb sees fresh attempt.

2. **Passthrough HTTP contract** (`contracts/passthrough-http.md`):
   - One row per OMP verb with HTTP method, URL template, request body type, response type, error mapping (4xx → `InvalidRequestError`, 5xx → `ProviderError`, OMP envelope → typed subclass).
   - Capability gate: `verb not in self._capabilities.verbs` → `UnsupportedCapabilityError` raised before HTTP call (FR-009).

3. **Translation adapter mappings** (`contracts/mem0-mapping.md`, `contracts/supermemory-mapping.md`, `contracts/letta-mapping.md`):
   - Per-verb mapping table: OMP input → provider call → OMP output.
   - Field translation rules including `scope` → tag-prefix fallback (EC-006), embeddings managed by provider → `embedding_model` omitted + capability flagged (EC-007), pagination → opaque `next_cursor` (EC-005).
   - Error map: provider exception class → `OMPError` subclass.
   - Capability payload (verbs supported, scope mode, e2e, etc.).

4. **Test fixture contract** (in `data-model.md`):
   - `tests/conftest.py` adds `passthrough_adapter` (uses httpx `MockTransport` pointing to a tiny in-process OMP shim), `mem0_adapter`, `supermemory_adapter`, `letta_adapter` fixtures. Each is module-scoped and skips if its credentials env var is missing (in live mode) or its mock recording is missing.
   - `adapter` fixture `params` becomes `["postgres", "passthrough", "mem0", "supermemory", "letta"]`; dispatch dict updated. **No edits in any `test_contract_*.py` file** (SC-005).
   - Single fixture in `conftest.py` — `_omp_mock_server` — provides the in-process OMP HTTP shim (FastAPI-free: pure `httpx.MockTransport` + dispatch dict) so passthrough tests run offline.

5. **Quickstart** (`quickstart.md`):
   - Install commands for each adapter (`pip install openmem[mem0]`, etc.).
   - Minimal runnable snippets per provider matching SPEC §11 idiom.
   - How to run the contract suite for a new adapter.
   - How to opt into live mode vs default mock mode.

6. **Agent context update**: Update the plan reference between `<!-- SPECKIT START -->` and `<!-- SPECKIT END -->` markers in `.github/copilot-instructions.md` to point to this `plan.md`.

## Constitution Check (post-design re-evaluation)

After Phase 1 design above, re-checking:

| Principle | Re-check result |
|---|---|
| I. Spec-First | ✅ Passthrough mapping (`contracts/passthrough-http.md`) is mechanical from OpenAPI; no new types invented. |
| II. Conformance | ✅ Test fixture contract proves `test_contract_*.py` files are untouched; new adapter unit tests live in `tests/adapters/`. |
| III. Compat | ✅ Pool args are keyword-only with defaults; no required field/verb removed; CHANGELOG entry planned. |
| IV. Neutrality | ✅ All translation adapters opt-in via extras; Postgres path keeps zero-vendor stance. |
| V. Extensibility | ✅ All `x-<provider>` data round-trips via existing `extra="allow"`. |

✅ Gate still passes. **Complexity Tracking** table below remains empty.

## Verification

1. **Pool smoke test** — `pytest sdk-python/tests/adapters/test_postgres_pool.py -q` passes; the original `test_concurrent_inserts_do_not_deadlock` finishes in <30s (SC-002).
2. **Throughput benchmark** — script in `tests/adapters/test_postgres_pool.py::test_pool_5x_throughput` measures ≥5× (SC-001).
3. **Passthrough conformance** — `pytest -k passthrough` runs the full contract suite against the in-process MockTransport OMP shim; 100% green (SC-003).
4. **Translation adapters** — `pytest -k "mem0 or supermemory or letta"` runs contract suite against recorded fixtures (default) or live providers (env-gated); 100% green for advertised verbs; mismatches raise `UnsupportedCapabilityError` (SC-004).
5. **Diff size of `test_contract_*.py`** — `git diff --stat HEAD~1 sdk-python/tests/test_contract_*.py` is empty (SC-005).
6. **Full-suite timing** — `pytest sdk-python/tests -q` completes in <5 min on CI (SC-006); no test exceeds the 30s default unless explicitly overridden.
7. **Error-type purity** — `tests/adapters/test_*_mapping.py::test_provider_errors_translate` asserts every captured upstream exception is wrapped in an `OMPError` subclass (SC-007).
8. **Example runs end-to-end** — `python examples/02_switch_providers.py` prints comparable outputs from both providers (SC-008).
9. **Spec parity preserved (Principle I)** — `pytest sdk-python/tests/test_types_match_openapi.py` still passes.
10. **CHANGELOG** — `0.2.0` entry lists pool, passthrough, three adapters, RLock removal (EC-009), `pytest-timeout`.

## Complexity Tracking

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| *(none)* | | |
