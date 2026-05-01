# Implementation Plan: M2.1 — Live-API bridges for translation adapters

**Branch**: `003-m2-1-live` | **Date**: 2026-04-28 | **Spec**: [spec.md](spec.md)
**Constitution**: [.specify/memory/constitution.md](../../.specify/memory/constitution.md) (v1.0.2)

## Summary

M2 shipped three translation adapters (mem0, supermemory, letta) whose mock-mode
contract tests pass (158/2). When pointed at the providers' live APIs they
break for all three: mem0 is asynchronous and LLM-rewrites content;
supermemory's real base URL is `/v3` (not `/v1`), uses camelCase, and is
also async; letta's `passages.create` returns a list of auto-chunked
passages, has no `passages.retrieve`, and uses `top_k=` not `limit=`.

M2.1 closes that wire-boundary gap by:

1. Rewriting each translation adapter against the real upstream surface
   (mem0ai 2.x, supermemory REST `/v3`, letta-client 1.10).
2. Adding an optional `status` enum (`queued | indexing | done | failed`)
   to the OMP `Memory` schema so async ingestion is first-class.
3. Adding a bounded poll inside `get(id)` (default 60 s, env `OMP_INGEST_TIMEOUT`)
   that resolves async records or raises `ProviderError(code="ingestion_timeout")`.
4. Preserving original user content under `x-{provider}.original_content`
   when the provider rewrites it, with a new contract test
   `test_add_then_search_finds_original_content` that asserts findability.
5. A live/mock fixture switch gated by `OMP_LIVE=1` AND the matching
   `*_API_KEY`; default `pytest` runs stay 100% mock-mode (M2 baseline
   preserved). Live tests register finalizers that clean up remote state.

No new packages. The reference path (Postgres + pgvector) is untouched.
Constitution Principle II is honoured: adding live-mode does NOT edit
`test_contract_*.py`; the switch lives in `conftest.py` fixtures and a
new pytest marker.

## Technical Context

**Language/Version**: Python 3.11+ (M1/M2 baseline)
**Primary Dependencies**: `mem0ai>=2.0,<3` (was `>=0.1`), `letta-client>=1.10` (was generic `letta-client`), supermemory still REST via `httpx` (no SDK); pydantic v2; existing `psycopg`, `psycopg-pool`, `pgvector` unchanged
**Storage**: Postgres + pgvector for the reference path; remote provider state for live-mode tests (cleaned up per-test)
**Testing**: pytest, pytest-cov, pytest-timeout (M2-shipped); per-test default 30 s, raised to 90 s for `@pytest.mark.live`
**Target Platform**: Linux/macOS/Windows dev + CI; Python library
**Project Type**: Single-project Python library (`sdk-python/openmem/`) — no layout change
**Performance Goals**: Live `add()` returns within 5 s wall-clock (FR-102 acceptance); `get(id)` polling overhead bounded to a single 60 s budget per call
**Constraints**: Zero edits to `test_contract_*.py` (Principle II / SC-005, SC-109); zero new vendor coupling on reference path (Principle IV); mock-mode default preserved (SC-104); cleanup failures are warnings, not red builds (EC-105)
**Scale/Scope**: Same five adapters as M2 (`postgres`, `passthrough`, `mem0`, `supermemory`, `letta`). One new spec field (`Memory.status`). One new contract test. ~3 modified adapter modules, ~1 modified `conftest.py`, ~3 new live-only test files (`test_*_live.py`).

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-checked at the bottom of this document after Phase 1 design.*

| Principle | How this plan satisfies it |
|---|---|
| I. Spec-First, Single Source of Truth (NON-NEGOTIABLE) | The new `status` field is added to `spec/omp-0.1.openapi.yaml` Memory schema FIRST (FR-122), then to `openmem.types.Memory`, then to adapter outputs. No adapter ships a status value the spec does not enumerate. The new contract test `test_add_then_search_finds_original_content` is derived from the spec invariant "search MUST find content the user added" (Principle III: backward semantics preserved). |
| II. Adapter Conformance via Shared Contract Tests (NON-NEGOTIABLE) | Live mode plugs into the **same** parametrized fixture (`adapter` in `conftest.py`) — no `test_contract_*.py` file is edited (SC-109). The new `test_add_then_search_finds_original_content` lives in `test_contract_search.py` and runs for every advertised-search adapter. Capability-aware skips already in conftest cover Letta's missing `get`/`update`. |
| III. Backward and Forward Compatibility | `Memory.status` is **additive optional** (default `None`); existing M1/M2 clients ignore it. No verb signature changes. No required field removed. The `OMPError`/`ProviderError` taxonomy is unchanged (the new `code="ingestion_timeout"` is a new enum value of an existing field — additive). The 60 s poll budget is configurable; default preserves the "add returns the canonical record" feel for synchronous providers. |
| IV. Provider Neutrality and User Sovereignty | Postgres remains the reference path with no behavioural change (it always reports `status="done"`). Live mode is **opt-in** (`OMP_LIVE=1` + key); CI default and contributor laptops stay mock. No telemetry; API keys are loaded from `.env` only and never logged (M2 invariant carried forward). |
| V. Open Extensibility via Namespaced Fields | Original user content lives under `x-mem0.original_content` (and equivalents for any other future LLM-rewriting provider). All passage ids for Letta auto-chunked memories are stashed under `x-letta.passage_ids`. Standard fields are never overridden by extension data. |

**Constitution gate result**: ✅ Pass. No violations; **Complexity Tracking** table at the bottom is empty.

## Project Structure

### Documentation (this feature)

```text
specs/003-m2-1-live/
├── plan.md                       # This file (/speckit.plan)
├── research.md                   # Phase 0 — async-ingestion + cleanup decisions
├── data-model.md                 # Phase 1 — Memory.status, async record, id mappings
├── quickstart.md                 # Phase 1 — install/setup/run live mode for each provider
├── contracts/
│   ├── mem0-mapping.md           # OMP verb ↔ mem0ai 2.x SDK calls (UPDATED from M2)
│   ├── supermemory-mapping.md    # OMP verb ↔ supermemory REST /v3 (UPDATED from M2)
│   └── letta-mapping.md          # OMP verb ↔ letta-client 1.10 (UPDATED from M2)
├── spec.md                       # (already exists)
└── tasks.md                      # /speckit.tasks output (NOT created here)
```

### Source Code (repository root)

```text
spec/
└── omp-0.1.openapi.yaml          # MODIFIED: add Memory.status enum (FR-122)

sdk-python/
├── openmem/
│   ├── types.py                  # MODIFIED: Memory gains optional status: Literal[...]
│   ├── errors.py                 # MODIFIED: register code="ingestion_timeout" (additive)
│   └── adapters/
│       ├── mem0.py               # MODIFIED: rewrite for mem0ai 2.x async + LLM-rewrite handling
│       ├── supermemory.py        # MODIFIED: base URL → /v3; POST list/search; camelCase mapping
│       ├── letta.py              # MODIFIED: list-of-passages handling; capabilities exclude get/update
│       └── _ingest.py            # NEW: shared bounded-poll helper (used by mem0 + supermemory)
├── tests/
│   ├── conftest.py               # MODIFIED: live/mock fixture switch + finalizers + live marker
│   ├── adapters/
│   │   ├── test_mem0_live.py     # NEW: @pytest.mark.live coverage of async + rewrite + timeout
│   │   ├── test_supermemory_live.py  # NEW
│   │   └── test_letta_live.py    # NEW
│   ├── test_contract_search.py   # MODIFIED: add test_add_then_search_finds_original_content
│   └── test_contract_*.py        # OTHERWISE UNCHANGED (Principle II / SC-109)
└── pyproject.toml                # MODIFIED: pin mem0ai>=2.0,<3 ; letta-client>=1.10 in extras

examples/
└── 02_switch_providers.py        # MODIFIED: drop unsupported verbs gracefully; print provider+status

CHANGELOG.md                      # MODIFIED: M2.1 release notes (status field, live mode, breaking-for-asyncs)
```

**Structure Decision**: Single-project Python library — same layout as M1/M2. No new top-level dirs.

## Phases

### Phase 0 — Research → produces [research.md](research.md)

Decisions consolidated:

1. **Async-ingestion contract** — `add` returns immediately with `status="queued"`; `get` polls with bounded budget. Alternatives (block in `add`, fire-and-forget) rejected.
2. **LLM-rewrite preservation** — original under `x-{provider}.original_content`; new contract test asserts findability via search. Alternatives (refuse to ingest if rewrite, return both) rejected.
3. **Live/mock test switch** — `OMP_LIVE=1` AND matching `*_API_KEY`; per-provider granularity. Alternatives (single global switch, separate test tree) rejected.
4. **Cleanup strategy** — pytest fixture finalizer per memory id; failures logged not raised. Alternatives (suite-level teardown, dedicated cleanup CLI) rejected.
5. **Letta auto-chunking** — first passage id is canonical; all ids in `x-letta.passage_ids`; `delete` removes them all. Alternatives (one OMP memory per chunk, refuse long content) rejected.
6. **Spec evolution discipline** — `Memory.status` additive optional; updated FIRST in OpenAPI, then in Pydantic, then in adapters (Principle I).

### Phase 1 — Design & Contracts → produces [data-model.md](data-model.md), [contracts/](contracts/), [quickstart.md](quickstart.md)

1. **Memory.status enum** in [data-model.md](data-model.md):
   - Values: `queued | indexing | done | failed | None` (None = legacy / not reported).
   - Per-adapter mapping: postgres always `done`; passthrough → mirror upstream; mem0 → `queued` until `get` finds it then `done`; supermemory → upstream `status` field; letta → `done` (synchronous).

2. **Async-ingestion record** in [data-model.md](data-model.md):
   - Returned by `add` when provider is async. `id` is the upstream id; `content` is the *original* user content; `x-{provider}.original_content` mirrors it; `status="queued"`.

3. **Provider-id ↔ OMP-id mapping** in [data-model.md](data-model.md):
   - mem0: UUID, used as-is.
   - supermemory: 22-char base62, used as-is.
   - letta: encoded as `mem_{agent_id}_{first_passage_id}`; `x-letta.passage_ids` lists all chunk ids.

4. **Per-provider mapping contracts** in [contracts/](contracts/):
   - One file per adapter, format mirrors M2's contracts: row per OMP verb with the exact SDK / REST call, request shape, response shape, and error translation.
   - These supersede the M2 contracts for adapters that drift.

5. **Quickstart** ([quickstart.md](quickstart.md)):
   - Per-provider key acquisition + `.env` line.
   - Single-command live-mode invocation: `OMP_LIVE=1 MEM0_API_KEY=... pytest -k mem0`.
   - Demo: `python examples/02_switch_providers.py`.

6. **Agent context update**: Update the plan reference between the
   `<!-- SPECKIT START -->` and `<!-- SPECKIT END -->` markers in
   `.github/copilot-instructions.md` to point to this plan
   (`specs/003-m2-1-live/plan.md`).

## Constitution Check (post-Phase-1 re-evaluation)

| Principle | Status after design |
|---|---|
| I. Spec-First | ✅ — `Memory.status` added to OpenAPI before Pydantic; data-model.md cites the spec change as gating. |
| II. Contract Tests | ✅ — Live mode swaps fixtures, never test bodies. New search test runs across all advertised-search adapters. |
| III. Compatibility | ✅ — `status` optional default-None; existing clients unaffected. Error code `ingestion_timeout` is additive enum value. |
| IV. Provider Neutrality | ✅ — Reference path unchanged; live mode opt-in; key handling untouched. |
| V. Extensibility | ✅ — Original content + passage ids ride under `x-{provider}.*`. |

**Result**: ✅ Pass — no entries in Complexity Tracking.

## Complexity Tracking

> **Empty.** No constitution-violating complexity introduced.

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| (none) | — | — |
