# Implementation Plan: M3.2 Async facade + FastAPI passthrough server

**Branch**: `005-async-fastapi` | **Date**: 2026-05-01 | **Spec**: [spec.md](spec.md)
**Input**: Feature specification from `specs/005-async-fastapi/spec.md`

## Summary

Add an `AsyncMemory` facade that mirrors `Memory` 1:1 with `async def` verbs and a FastAPI passthrough server (`omp-server`) that mounts the OpenAPI surface 1:1. Postgres and passthrough adapters get **native** async implementations (`asyncpg`, `httpx.AsyncClient`); the three remote SDK adapters (mem0, supermemory, letta) are wrapped via `asyncio.to_thread` because their upstream SDKs are sync-only. Existing sync `Memory` is **untouched** — the async surface is additive. Cancellation propagates end-to-end and aborts in-flight backend work on async-native adapters.

**Per user direction**: ship as **two PRs on the same `005-async-fastapi` branch** (sequential, not parallel):

| PR | Scope | Stories satisfied | Gates |
|---|---|---|---|
| **PR-A — AsyncMemory + cancellation** | `openmem.AsyncMemory`, `AsyncBaseAdapter`, async postgres + passthrough adapters, threadpool wrapper for mem0/supermemory/letta, `[async]` extra | US1, US3, US4 (compat) | Existing 365 sync tests still pass; new async-conformance suite green; cancellation test shows pool-release ≤500 ms |
| **PR-B — FastAPI passthrough server** | `openmem.server` package, `omp-server` console script, OpenAPI conformance test suite, `[server]` extra, error-mapping table | US2 | OpenAPI conformance 100%; `omp-server` boots <2 s; SC-001 RPS benchmark passes |

PR-A is a hard prerequisite for PR-B (the server holds an `AsyncMemory`). PR-B opens against PR-A's merge commit.

## Technical Context

**Language/Version**: Python ≥3.11 (matches existing `requires-python`)
**Primary Dependencies**:
- PR-A: `asyncpg>=0.29` (async postgres), `httpx>=0.27` (async via `httpx.AsyncClient`; already a sync dep, version-bump only), stdlib `asyncio` + `concurrent.futures`
- PR-B: `fastapi>=0.115`, `uvicorn[standard]>=0.30`, existing `pydantic>=2` types from M1
**Storage**: Reuses existing per-provider stores (postgres+pgvector, mem0/supermemory/letta hosted)
**Testing**: pytest 9.x + `pytest-asyncio>=0.24` (new dev dep) + `httpx.AsyncClient` (test client for FastAPI)
**Target Platform**: Linux/macOS/Windows; Python ≥3.11. Server: any ASGI host (uvicorn primary).
**Project Type**: Library + optional HTTP server (single Python project, two packaging extras)
**Performance Goals**: SC-001 ≥10× sync RPS; SC-002 100 concurrent adds ≤2× single-add latency; SC-003 cancellation pool-release ≤500 ms; SC-006 server boot <2 s
**Constraints**: Zero change to sync `Memory` public surface (FR-011); unauthenticated server (auth deferred); no new OpenAPI fields (consume `spec/omp-0.1.openapi.yaml` as-is); Python 3.11 minimum to allow `asyncio.TaskGroup` and structured cancellation
**Scale/Scope**: ≈1500 LOC implementation across PRs (≈900 PR-A, ≈600 PR-B); ≈40 new tests; +2 packaging extras

## Constitution Check

| Principle | How this plan satisfies it |
|---|---|
| **I. Spec-First, Single Source of Truth (NON-NEGOTIABLE)** | Zero changes to `spec/omp-0.1.openapi.yaml`. The FastAPI server **consumes** the spec — every route, request schema, response schema, and error code is mounted from the existing OpenAPI document. A conformance test (PR-B) asserts every server response validates against the spec's schemas. AsyncMemory adds no new verbs/fields/error codes. |
| **II. Adapter Conformance via Shared Contract Tests (NON-NEGOTIABLE)** | A new `tests/async/test_async_contract_*.py` suite mirrors the existing sync `tests/test_contract_*.py` files and parametrizes over every adapter. Each async adapter MUST pass the same lifecycle/search/errors/compat assertions as its sync counterpart. The threadpool-wrapped adapters reuse the existing sync adapter for the actual call but still run the async contract suite to prove the wrapper preserves semantics. |
| **III. Backward and Forward Compatibility** | `Memory` is **byte-identical** post-change (FR-011, SC-008). `AsyncMemory` is purely additive — no rename, no signature change. Both extras (`[async]`, `[server]`) are opt-in; a bare `pip install openmem` continues to install only the existing sync stack (FR-025, SC-007). No required field/verb removed. Unknown fields still tolerated (we use the existing pydantic models unchanged). |
| **IV. Provider Neutrality and User Sovereignty** | The server defaults to **postgres** (reference path) and works with no third-party account. No vendor coupling: AsyncMemory supports all five existing providers from day one. `user_id` and scoping primitives unchanged; the server enforces empty-`user_id` rejection at the boundary (FR-016 reuses `code = invalid_request`). The server is unauthenticated by design — no telemetry, no license keys, no hosted dependency. |
| **V. Open Extensibility via Namespaced Fields** | No new top-level fields. All existing `x-mem0` / `x-supermemory` / `x-letta` extension fields pass through both `AsyncMemory` and the server unchanged because we reuse the existing pydantic models. The server does NOT strip extension fields on response. |

**Result**: All five principles satisfied. **No violations** → no Complexity Tracking entries needed.

## Project Structure

### Documentation (this feature)

```text
specs/005-async-fastapi/
├── plan.md                # This file
├── research.md            # Phase 0 — driver/cancellation/extras decisions
├── data-model.md          # Phase 1 — AsyncMemory & OmpServer entities + protocols
├── quickstart.md          # Phase 1 — usage walkthrough for both PRs
├── contracts/
│   ├── async-memory.md    # Phase 1 — AsyncMemory + AsyncBaseAdapter contract
│   └── http-server.md     # Phase 1 — HTTP route/error mapping contract
├── checklists/
│   └── requirements.md    # Already exists — passed all 16 items
└── tasks.md               # Phase 2 — generated by /speckit.tasks
```

### Source Code (repository root)

```text
sdk-python/
├── openmem/
│   ├── __init__.py                       # MODIFIED: lazy re-export AsyncMemory
│   ├── memory.py                         # UNCHANGED (FR-011)
│   ├── async_memory.py                   # NEW (PR-A): AsyncMemory facade
│   ├── adapters/
│   │   ├── async_base.py                 # NEW (PR-A): AsyncBaseAdapter protocol
│   │   ├── async_postgres.py             # NEW (PR-A): asyncpg-backed adapter
│   │   ├── async_passthrough.py          # NEW (PR-A): httpx.AsyncClient adapter
│   │   ├── async_threadwrap.py           # NEW (PR-A): wraps any sync adapter
│   │   ├── postgres.py                   # UNCHANGED
│   │   ├── passthrough.py                # UNCHANGED
│   │   ├── mem0.py / supermemory.py / letta.py   # UNCHANGED
│   │   └── _ingest.py / _cursor.py / _http.py    # UNCHANGED
│   └── server/                           # NEW (PR-B)
│       ├── __init__.py                   # exports `app`, `create_app(config)`
│       ├── app.py                        # FastAPI app factory + route registration
│       ├── routes.py                     # one function per OpenAPI path
│       ├── errors.py                     # exception → HTTP status + Error envelope
│       ├── deps.py                       # AsyncMemory dependency-injection
│       └── cli.py                        # `omp-server` console script entry
└── tests/
    ├── async/                            # NEW (PR-A)
    │   ├── test_async_contract_lifecycle.py
    │   ├── test_async_contract_search.py
    │   ├── test_async_contract_errors.py
    │   ├── test_async_facade.py
    │   ├── test_async_cancellation.py
    │   └── test_async_threadwrap.py
    └── server/                           # NEW (PR-B)
        ├── test_server_routes.py
        ├── test_server_errors.py
        ├── test_server_openapi_conformance.py
        ├── test_server_health.py
        └── test_server_cli.py
```

**Structure Decision**: **Single Python project with two packaging extras**. AsyncMemory and the server live alongside the existing sync stack inside the `openmem` package; their dependencies are gated by `[async]` and `[server]` extras (FR-025) so the bare install stays slim. No new top-level project / no monorepo split is justified — the server is a thin FastAPI wrapper over `AsyncMemory` and shares the same release cadence. This matches Option 1 (single project) from the template.

## Phase progress

- **Phase 0 (research)**: ✅ See [research.md](research.md) — async postgres driver, cancellation strategy, threadpool sizing, extras layout, OpenAPI mounting strategy.
- **Phase 1 (design)**: ✅ See [data-model.md](data-model.md), [contracts/async-memory.md](contracts/async-memory.md), [contracts/http-server.md](contracts/http-server.md), [quickstart.md](quickstart.md).
- **Phase 2 (tasks)**: deferred to `/speckit.tasks` (per workflow).
- **Agent context update**: `.github/copilot-instructions.md` repointed in this plan run to `specs/005-async-fastapi/plan.md`.

## Re-evaluated Constitution Check (post-design)

After producing data-model.md and the two contracts:

- **Principle I**: Re-affirmed. The `http-server.md` contract derives every route/status/code directly from `spec/omp-0.1.openapi.yaml`; no field is invented.
- **Principle II**: Re-affirmed. The async contract suite is a near-mechanical transform of the existing sync suite (`async def`, `await`, `pytest.mark.asyncio`); no new assertion classes.
- **Principle III**: Re-affirmed. Sync `Memory` and the existing adapters are untouched. The `__init__.py` change is a guarded lazy re-export that fails *only* when the `[async]` extra is missing, which is correct behavior, not a break.
- **Principle IV**: Re-affirmed. Postgres remains the default and the only fully-self-hosted path through both PRs.
- **Principle V**: Re-affirmed. No new top-level fields anywhere.

**Verdict**: All gates pass. Proceed to `/speckit.tasks`.

## Complexity Tracking

> No constitution violations. Table empty.

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| *(none)* | — | — |
