# Changelog

All notable changes to this project will be documented in this file.

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
