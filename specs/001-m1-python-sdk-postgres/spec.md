# Feature Spec: M1 — Python SDK skeleton + Postgres adapter

**Feature ID**: 001-m1-python-sdk-postgres
**Date**: 2026-04-28
**Status**: Draft (backfilled to close /speckit.analyze finding C1)
**Plan**: [plan.md](plan.md)
**Tasks**: [tasks.md](tasks.md)
**Authoritative protocol sources**: [spec/omp-0.1.openapi.yaml](../../omp-0.1.openapi.yaml), [SPEC_Version8.md](../../SPEC_Version8.md), [.specify/memory/constitution.md](../../.specify/memory/constitution.md)

> **Note on backfill.** The functional surface of this feature is the OMP
> protocol itself, which is already fully specified in the OpenAPI document
> and SPEC_Version8.md (the constitution names the OpenAPI doc as the
> single source of truth, Principle I). This `spec.md` exists to (a) record
> the user stories and success criteria for *this milestone* (M1) in the
> Spec Kit format, and (b) make traceability from FR-### / SC-### → tasks
> explicit. It does not redefine the protocol.

## Overview

Ship a `pip install`-able Python SDK (`openmem`) implementing the OMP v0.1
verbs on top of a Postgres + pgvector reference adapter. Validate
correctness via a parametrized contract test suite that all future adapters
must pass. Demonstrate the standard's promise (substitutability) with two
runnable examples.

## User Stories

### US1 — Quickstart works against Postgres (Priority: **P1**, MVP)

**As** a Python developer trying OMP for the first time
**I want** to `pip install openmem`, point it at a Postgres URL, and call
`mem.add / search / context / update / delete / list`
**so that** I get a working AI memory layer in under five minutes with no
provider lock-in.

**Acceptance criteria:**
- AC1.1 From a clean venv, `pip install -e sdk-python &&
  python examples/01_quickstart.py` succeeds with only `PG_URL` set.
- AC1.2 Each verb in the OpenAPI `paths` (except `/audit`) returns the
  documented response shape and respects the documented status codes.
- AC1.3 No `openai`, `mem0`, or other third-party SDK is required for the
  quickstart to run (Principle IV).

### US2 — Conformance suite proves substitutability (Priority: **P2**)

**As** an adapter author for a future memory backend (Mem0, Supermemory,
Notion, etc.)
**I want** to run a single command and get green/red signal that my
adapter is OMP-conformant
**so that** the "OMP Native / Compatible / Community" tier is determined
by tests, not by self-declaration (Principle II, NON-NEGOTIABLE).

**Acceptance criteria:**
- AC2.1 `pytest sdk-python/tests -q` exits 0 on a clean checkout (Docker
  available for the pgvector container).
- AC2.2 Test coverage on `openmem/adapters/postgres.py` is ≥ 90 %.
- AC2.3 The contract suite in `sdk-python/tests/test_contract*.py` is
  parametrized over an `adapter` fixture; adding a new adapter requires
  zero changes to the test files (only one new entry in the fixture's
  `params`).
- AC2.4 The suite includes positive checks for: full lifecycle
  (add → search → get → update → delete → list → context), the standard
  error envelope, capability advertisement, `x-<provider>` extension
  round-trip (Principle V), and forward-compatibility with unknown fields
  (Principle III).
- AC2.5 A separate test (`test_types_match_openapi`) asserts every
  `components/schemas` entry in `spec/omp-0.1.openapi.yaml` corresponds to
  a pydantic model with matching required fields (Principle I).

### US3 — Switch providers with zero code change (Priority: **P3**)

**As** an end-user of an app that uses OMP
**I want** to swap the memory backend (e.g., Postgres ↔ a future Mem0
deployment) by changing one configuration value
**so that** I am not locked into whichever provider the app developer
originally chose (SPEC §16 substitutability metric).

**Acceptance criteria:**
- AC3.1 `python examples/02_switch_providers.py` runs the same application
  code path against two `Memory(...)` instances and produces identical
  search-result ordering for the same query.
- AC3.2 The SDK auto-detects native vs. translation per SPEC §11a: when
  `base_url` is supplied and `/capabilities` returns `omp_version`, the
  passthrough adapter is used; otherwise the registered translation
  adapter for `provider=` is used; otherwise `UnsupportedProviderError` is
  raised.
- AC3.3 The capability probe is cached per `Memory` instance (one HTTP
  call per session, not per verb).

## Functional Requirements

Each FR maps to one or more OpenAPI operationIds in
[spec/omp-0.1.openapi.yaml](../../omp-0.1.openapi.yaml). The OpenAPI doc
is authoritative for request/response shapes; this list exists for task
traceability.

| FR | Maps to | Description |
|---|---|---|
| FR-001 | `addMemory` | SDK can create a memory with required fields `content`, `user_id` and optional standard fields. |
| FR-002 | `getMemory` | SDK can fetch a memory by id; returns `404 / NotFoundError` on miss. |
| FR-003 | `updateMemory` | SDK can update mutable fields and append to `supersedes`. |
| FR-004 | `deleteMemory` | SDK can delete a memory; returns `404 / NotFoundError` if absent. |
| FR-005 | `listMemories` | SDK can list memories filtered by `user_id`, scope glob, tag, time window; supports keyset pagination via `cursor` / `next_cursor`. |
| FR-006 | `searchMemories` | SDK can do semantic + keyword hybrid search; supports `min_score`. |
| FR-007 | `getContext` | SDK returns prompt-ready ranked text + citations + token estimate, respecting `token_budget`. |
| FR-008 | `getCapabilities` | SDK exposes the provider's capability matrix. |
| FR-009 | `getAudit` | SDK exposes audit log when the provider supports it; raises `UnsupportedCapabilityError` otherwise. |
| FR-010 | (cross-cutting) | All errors use the standard `Error` envelope (Principle II). |
| FR-011 | (cross-cutting) | `x-<provider>` extension fields round-trip on every memory (Principle V). |
| FR-012 | (cross-cutting) | Unknown future fields are silently preserved on every response model (Principle III). |
| FR-013 | (cross-cutting) | A `PassthroughAdapter` exists with a `_probe` method; full passthrough verbs are out of scope for M1. |
| FR-014 | (cross-cutting) | The `embedding_model` of every stored memory is recorded; cross-model search hard-fails (`InvalidRequestError`) — decision A from `plan.md` *Further Considerations*. |
| FR-015 | (cross-cutting) | Memory IDs are formatted as `mem_<ulid>` — decision A from `plan.md` *Further Considerations*. |

## Success Criteria (Buildable)

The following Success Criteria require buildable work and are mapped to
tasks. Post-launch outcome metrics from SPEC §16 (≥ 5 third-party
adapters in 3 months, organic GitHub growth, etc.) are excluded per the
analyze rubric.

| SC | Description | How verified |
|---|---|---|
| SC-001 | Quickstart works from a clean venv with only `PG_URL` set | Manual run; T040 |
| SC-002 | Contract suite is the single green/red gate | `pytest sdk-python/tests -q` in CI; T038 |
| SC-003 | Coverage ≥ 90 % on `openmem/adapters/postgres.py` | `pytest --cov-fail-under=90 --cov=openmem.adapters.postgres`; T029 |
| SC-004 | OpenAPI spec validates in CI | `omp-validate-spec` job; T028, T038 |
| SC-005 | Pydantic types match OpenAPI components | `test_types_match_openapi`; T025 |
| SC-006 | Substitutability E2E demo runs | `python examples/02_switch_providers.py`; T033 |
| SC-007 | Offline chatbot demo runs without any external account | `python examples/03_chatbot_demo/main.py` with no env vars beyond `PG_URL`; T034 |
| SC-008 | All Constitution gated checklist items (Principles I–V) reviewed in PR | Manual; T041 |

## Edge Cases

- **EC-001** Empty database: `search`, `list`, `context` return empty
  results with no error.
- **EC-002** `mem.delete(id)` for missing id raises `NotFoundError`
  (`code=not_found`, HTTP 404 equivalent).
- **EC-003** Cross-embedding-model search: storing with
  `text-embedding-3-small` then querying with `FakeEmbedder` raises
  `InvalidRequestError` (FR-014; decision A).
- **EC-004** Concurrent inserts: 10 threads × 50 inserts complete
  without deadlock (DDL is idempotent; UNIQUE on `id`).
- **EC-005** Embedding dimension mismatch at INSERT (e.g., adapter
  configured with dim=64 but caller passes a 1536-vector via
  `x-mem0.embedding`) raises `InvalidRequestError` *before* the SQL
  call.
- **EC-006** Unknown response field from a future provider is preserved
  on `Memory`, `Capabilities`, `SearchResult`, `MemoryPage`,
  `ContextBlock`, and `AuditEntry` via `extra="allow"` (FR-012).
- **EC-007** Pagination: `list(..., limit=50)` with > 50 stored returns
  `next_cursor`; supplying it returns the next page; the final page has
  `next_cursor=None`.
- **EC-008** `mem.audit(...)` called against the Postgres adapter
  raises `UnsupportedCapabilityError` (it advertises
  `supports_audit=False`).

## Out of Scope (M1)

- TypeScript SDK (M3)
- Mem0 / Supermemory translation adapters (M2)
- FastAPI passthrough server (deferred until M2/M3 demand)
- OAuth 2.1 + PKCE flows (deferred per SPEC §17)
- Audit log persistence (Postgres adapter advertises
  `supports_audit=False`)
- Graph queries, E2E encryption (deferred to v0.2 per SPEC §4)
- Multi-vault federation (SPEC §17 open question)
