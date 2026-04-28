# Changelog

All notable changes to this project will be documented in this file.

## [0.2.0] — Unreleased (M2)

### Added (US1 — Postgres pooling)

- `PostgresAdapter` now uses a `psycopg_pool.ConnectionPool` instead of a
  single connection guarded by an `RLock` (FR-001, FR-002, FR-003).
- New `__init__` kwargs `pool_min_size` (default `1`), `pool_max_size`
  (default `10`), and `pool_timeout` (default `30.0` seconds).
- New `close()` method that calls `self._pool.close()`; idempotent.
- Pool exhaustion (`psycopg_pool.PoolTimeout`) is mapped to
  `ProviderError("connection pool exhausted: ...", provider="postgres")`
  (FR-004, EC-001).
- New dependency: `psycopg-pool>=3.2`.

### Added (US2 — Native passthrough)

- `PassthroughAdapter` now implements every OMP verb against a native
  OMP HTTP endpoint per the verb→HTTP mapping in
  [`contracts/passthrough-http.md`](specs/002-m2-pool-passthrough-adapters/contracts/passthrough-http.md).
- A persistent `httpx.Client` is held on the instance (FR-007); tests
  inject `transport=` for in-process `MockTransport` shims.
- New `__init__` kwargs `transport` and `timeout`; new `close()` method.
- Capability gate: every verb calls `self._check_verb(verb)` before any
  network I/O. Unadvertised verbs raise `UnsupportedCapabilityError`
  with zero HTTP requests issued (FR-009, EC-003).
- Error decoding shared via `openmem.adapters._http`:
  OMP `Error` envelope → typed exception subclass; bare 4xx →
  `InvalidRequestError`; bare 5xx → `ProviderError` (FR-008, FR-010).
- Exactly one redirect is followed; a second 3xx raises `ProviderError`
  ("redirect loop") (EC-004).
- `Authorization: Bearer ...` is set on the client at construction time
  and never appears in log records (FR-011).
- `PassthroughAdapter` now passes the full shared contract suite via an
  in-process `httpx.MockTransport` backed by `PostgresAdapter`
  (SC-005: zero edits to `test_contract_*.py`).

### Added (US3 — Translation adapters)

_Filled by T031._

### Tooling

_Filled by T034._

### Removed / Behavior changes

- `PostgresAdapter._lock` and the `@_synchronized` decorator are removed
  (EC-009). The pool replaces them; callers depending on the lock
  attribute will see `AttributeError`. No public API break.

## [0.1.0] — Initial M1 release

### Added (Setup)
- Repository layout: `spec/`, `sdk-python/`, `examples/`, `.github/`
- Canonical OpenAPI spec at `spec/omp-0.1.openapi.yaml`
- Narrative spec at `spec/OMP-0.1.md`
- `pyproject.toml` for the `openmem` package (Python ≥3.11, hatchling)
- Constitution v1.0.2 with 5 principles (2 NON-NEGOTIABLE)

### Added (US1 — Quickstart works)
- `openmem.types` — pydantic v2 models mirroring every OpenAPI schema,
  with `_OMPBase(extra="allow")` on every response model (Principles
  III + V)
- `openmem.errors` — full hierarchy + `OMPError.from_response_dict`
- `openmem.adapters.base.BaseAdapter` — adapter ABC
- `openmem.adapters.embedder` — `Embedder` Protocol, `FakeEmbedder`
  (deterministic, offline), `OpenAIEmbedder` (lazy import)
- `openmem.adapters.postgres.PostgresAdapter` — Postgres + pgvector
  reference adapter with idempotent DDL, keyset pagination,
  cross-embedding-model hard-fail, `mem_<ulid>` ids
- `openmem.memory.Memory` — public facade
- `examples/01_quickstart.py` — runnable port of SPEC §11

### Added (US2 — Conformance gate)
- `tests/test_contract_lifecycle.py` — CRUD round-trips
- `tests/test_contract_search.py` — search + context behavior
- `tests/test_contract_errors.py` — error envelope + capability checks
- `tests/test_contract_compat.py` — `extra="allow"` on every response,
  `x-<provider>` round-trips
- `tests/test_types_match_openapi.py` — Principle I enforcement
- `tests/adapters/test_postgres_specific.py` — DDL idempotence,
  concurrency, dim mismatch, cross-model search
- `omp-validate-spec` console script

### Added (US3 — Substitutability)
- `openmem.adapters.passthrough.PassthroughAdapter` — capability probe
  (verbs are stubs; native verb forwarding lands in M2)
- `openmem.memory._resolve_adapter` — full SPEC §11a auto-detection
- `examples/02_switch_providers.py`, `examples/03_chatbot_demo/`

### Added (Polish)
- Root `README.md`, `sdk-python/README.md`
- `.github/workflows/ci.yml` (Python 3.11+3.12, two coverage gates)
- `CHANGELOG.md` (this file)

### Known limitations
- `PassthroughAdapter` only implements `_probe` and `capabilities`;
  every other verb raises `NotImplementedError("...lands in M2")`.
- Mem0, Supermemory, Letta translation adapters are not in M1.
