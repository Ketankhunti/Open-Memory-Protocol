# Changelog

All notable changes to this project will be documented in this file.

## [0.2.1] — Unreleased (M2.1 — Live-API bridges)

### Added

- **`Memory.status`** (additive, optional) — `"queued" | "indexing" | "done" | "failed" | None`.
  Mirrored in OpenAPI (`spec/omp-0.1.openapi.yaml`) and the Pydantic model
  (`sdk-python/openmem/types.py`). Legacy callers see `None` and continue to work.
- **`Error.code = "ingestion_timeout"`** (additive) for bounded-poll exhaustion
  on async-ingestion providers (mem0, supermemory). Reuses `ProviderError`.
- **`openmem.adapters._ingest`** — `poll_until(fn, timeout, *, base_delay, max_delay, ...)`
  helper with exponential back-off (`min(max_delay, base_delay * 2**n)`) and
  strict timeout enforcement. `OMP_INGEST_TIMEOUT` env var (default 60 s,
  positive int in `(0, 600]`; out-of-range → default + warning).
- **`openmem.adapters._cursor`** — opaque pagination cursor codec
  (`base64-urlsafe(json({"page": N}))`) with hard caps
  (`MAX_PAGE_NUMBER=10_000`, length ≤ 256 bytes). Defends against
  cursor-injection attacks; malformed cursors raise `InvalidRequestError`
  BEFORE any upstream call.
- **Live-test harness**: `OMP_LIVE=1` plus per-provider `*_API_KEY` env vars
  swap mock fixtures for real adapters; `@pytest.mark.live` tests
  auto-skip when off. Per-test finalizers track every created memory id and
  call `adapter.delete(id)` at teardown (FR-119). API keys are NEVER logged.
- **429 retry helper** in `tests/conftest.py` (`retry_once_on_rate_limit`)
  honours `retry_after` (capped at 30 s) and retries exactly once.

### Changed (per-provider rewrites)

- **`Mem0Adapter`** — full rewrite for `mem0ai>=2.0,<3`:
  - `add` posts `messages=[{role, content}]` and returns
    `Memory(id=event_id, status="queued", x-mem0={event_id, original_content})`.
  - `get` polls via `_ingest.poll_until`; on timeout raises
    `ProviderError(code="ingestion_timeout")`.
  - `list` uses `version="v2"` + page-based cursor codec.
  - `search` uses `version="v2"` and a strict pre-flight `user_id` check.
  - LLM-rewrite preserved via `x-mem0.original_content`.
- **`SupermemoryAdapter`** — full rewrite for the public `/v3` API:
  - Default `base_url = "https://api.supermemory.ai/v3"`; overridable via
    `SUPERMEMORY_BASE_URL`.
  - `list` uses `POST /memories/list`; `search` uses `POST /search`
    (chunk-shaped response).
  - `Memory.user_id` is read **only** from `metadata.user_id`
    (top-level `userId` is ignored — defends against cross-user metadata
    spoofing).
  - `update` excluded from advertised verbs and raises
    `UnsupportedCapabilityError` BEFORE any HTTP call.
- **`LettaAdapter`** — full rewrite for `letta-client>=1.10`:
  - Constructor uses `api_key=` (was `token=`).
  - `add` returns `list[Passage]`; the OMP `Memory.id` encodes the FIRST
    passage id; ALL passage ids stash under `x-letta.passage_ids` for
    fan-out delete.
  - `delete` iterates over EVERY passage id; the kwarg name is
    introspected at init via `inspect.signature(passages.delete)`.
  - `search` uses `top_k=` (NOT `limit=`).
  - `get` and `update` are no longer advertised; both raise
    `UnsupportedCapabilityError` BEFORE any network call.
  - Per-`user_id` agent cache with invalidate-and-retry on `NotFound`.

### Security

- API keys never appear in log records (verified by `test_no_credentials_in_logs`).
- Cursor strings cannot be smuggled to upstream (passthrough rejects
  oversized cursors at the boundary).
- Empty / whitespace-only `user_id` rejected BEFORE any upstream call on
  every adapter — defends cross-user scoping.
- Strict env-var parsing (`OMP_LIVE` requires exact `"1"`; `*_API_KEY`
  whitespace-only treated as unset).

### Breaking (semantic, no API change)

- `Mem0Adapter.add` and `SupermemoryAdapter.add` no longer guarantee
  `status="done"` on return — async ingestion now surfaces as
  `status="queued"`. Callers that need synchronous semantics should poll
  via `get(id)` or rely on the bounded-poll budget.

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

- `Mem0Adapter` (provider `"mem0"`, install via `pip install openmem[mem0]`).
  Verbs: `add`, `get`, `update`, `delete`, `list`, `search`, `context`. Scopes
  flatten to `tags` (EC-006). Audit and provider-managed embedding model are
  not advertised (EC-007). Lazy-imports `mem0ai` so the dependency is optional.
- `SupermemoryAdapter` (provider `"supermemory"`). Verbs: `add`, `get`,
  `delete`, `list`, `search`, `context` — `update` is intentionally not
  advertised and raises `UnsupportedCapabilityError`. REST-backed via the
  shared `_http.make_client` factory; scopes encoded as a `tag` prefix
  (EC-006). Search `min_score` maps to Supermemory's `threshold` parameter.
- `LettaAdapter` (provider `"letta"`, install via `pip install openmem[letta]`).
  Verbs: `add`, `get`, `delete`, `list`, `search`, `context`. Caches
  one Letta agent per `user_id` (`omp_{user_id}`); OMP ids are encoded as
  `mem_{agent_id}_{passage_id}` and decoded on `get`/`delete`. Native scopes
  via Letta passage metadata; temporal queries supported.
- All three adapters round-trip provider-namespaced `x-*` extension keys
  (Principle V) and pass the shared contract suite (`test_contract_*.py`)
  with zero edits to the contract files (SC-005).

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
