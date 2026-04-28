# Data Model — M2

Entities introduced or significantly modified. Standard `Memory`, `MemoryInput`, `MemoryUpdate`, `Capabilities`, etc. are unchanged from M1.

## E-001 · `PostgresAdapter` connection-pool surface

| Field / arg | Type | Default | Notes |
|---|---|---|---|
| `pool_min_size` | `int` | `1` | Constructor kwarg. Minimum live connections kept warm. |
| `pool_max_size` | `int` | `10` | Constructor kwarg. Hard cap on concurrency. |
| `pool_timeout` | `float` (seconds) | `30.0` | Constructor kwarg. Time to wait for a connection before raising `ProviderError("connection pool exhausted")`. |
| `_pool` | `psycopg_pool.ConnectionPool` | (constructed in `__init__`) | Replaces `self._conn` and `self._lock` from M1. |
| `_lock` | — | — | **REMOVED** (FR-003, EC-009). |
| `_synchronized` | — | — | **REMOVED** (FR-003). |

**State transitions** (per verb call):
1. `with self._pool.connection() as conn:` checks out a connection.
2. `with conn.cursor() as cur:` runs the SQL.
3. On normal return: connection auto-returned to pool.
4. On `psycopg.OperationalError`: pool marks connection broken, opens replacement on next checkout (FR-005).
5. On `pool.PoolTimeout`: wrapped to `ProviderError` (FR-004, EC-001).

**Invariants**:
- `__init__` MUST instantiate the pool *eagerly* but allow lazy connection opening (matches `psycopg_pool` default `open=True, configure=None`).
- `close()` MUST close the pool to release sockets (idempotent).

## E-002 · `PassthroughAdapter` runtime state

| Field | Type | Notes |
|---|---|---|
| `_base_url` | `str` | rstrip-`/`-normalized. |
| `_api_key` | `str | None` | Sent as `Authorization: Bearer …` (FR-011); never logged. |
| `_capabilities` | `Capabilities | None` | Cached after first probe. Read by every verb's capability gate (FR-009). |
| `_client` | `httpx.Client` | Persistent; constructed once per adapter; closed in `close()`. |

**State transitions** (per verb call):
1. Capability gate: if `verb not in self._capabilities.verbs` → raise `UnsupportedCapabilityError` (no network).
2. Build request via the table in [contracts/passthrough-http.md](contracts/passthrough-http.md).
3. `self._client.request(...)`; follow at most one 3xx redirect (EC-004).
4. On 2xx: parse body into the typed pydantic model from `openmem.types`.
5. On 4xx/5xx: try to parse OMP `Error` envelope first; if present, `OMPError.from_response_dict`. Otherwise 4xx → `InvalidRequestError`, 5xx → `ProviderError`. (FR-008, FR-010, EC-004.)

## E-003 · Translation adapter common shape

Every translation adapter (`Mem0Adapter`, `SupermemoryAdapter`, `LettaAdapter`) shares this shape:

| Component | Responsibility |
|---|---|
| `__init__(api_key, **config)` | Construct the underlying SDK / httpx client; store config. |
| `_to_provider_input(MemoryInput) -> dict` | Map OMP fields to provider fields. Apply `scope` → tag-prefix fallback (EC-006) when `capabilities.scopes == "tags"`. Drop `embedding_model` when provider auto-manages embeddings (EC-007). |
| `_from_provider_output(dict) -> Memory` | Reverse mapping. Stash any provider-only fields under `x-<provider>` per Principle V. |
| `_translate_error(exc) -> OMPError` | Catch every documented provider exception class; raise the matching `OMPError` subclass (FR-014, SC-007). |
| `capabilities() -> Capabilities` | Hard-coded, source-of-truth per the table in [research.md](research.md) §R-005. |

## E-004 · Test fixture contract

`sdk-python/tests/conftest.py` adds (without changing existing fixtures):

| Fixture | Scope | Purpose |
|---|---|---|
| `_omp_mock_server` | session | In-process OMP HTTP shim built on `httpx.MockTransport`; backs `passthrough_adapter`. |
| `passthrough_adapter` | module | `PassthroughAdapter(base_url=..., transport=_omp_mock_server)`. |
| `mem0_adapter` | module | Real `Mem0Adapter` if `MEM0_API_KEY` env var is set; otherwise patched with recorded fixtures. |
| `supermemory_adapter` | module | Same pattern, `SUPERMEMORY_API_KEY`. |
| `letta_adapter` | module | Same pattern, `LETTA_API_KEY`. |

The `adapter` fixture's `params` becomes:

```python
@pytest.fixture(params=["postgres", "passthrough", "mem0", "supermemory", "letta"])
def adapter(request, postgres_adapter, passthrough_adapter,
            mem0_adapter, supermemory_adapter, letta_adapter):
    return {
        "postgres": postgres_adapter,
        "passthrough": passthrough_adapter,
        "mem0": mem0_adapter,
        "supermemory": supermemory_adapter,
        "letta": letta_adapter,
    }[request.param]
```

**Constraint**: No file under `sdk-python/tests/test_contract_*.py` is modified. (SC-005 verification: `git diff --stat` MUST be empty for those paths after M2 lands.)

## E-005 · Capability negotiation flow (with translation adapters)

1. Test parameterizes over `adapter`.
2. Each test that requires a verb calls `adapter.capabilities()` once.
3. If verb is missing from `capabilities.verbs`, the test asserts `UnsupportedCapabilityError` is raised on call (FR-009 / SC-004).
4. If verb is present, the standard contract assertions run.

This is implemented today in `test_contract_compat.py` and requires zero changes to extend to new adapters.
