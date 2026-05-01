# Phase 1 Data Model — M3.2 Async facade + FastAPI server

This document defines the entities, protocols, and invariants for the M3.2 implementation. No new schema fields are introduced; all data structures reuse the existing pydantic models from `openmem/types.py`.

---

## 1. `AsyncMemory` (PR-A)

The user-facing async facade. Mirrors `Memory` 1:1.

### Public surface

```python
class AsyncMemory:
    def __init__(
        self,
        provider: Literal["postgres", "passthrough", "mem0", "supermemory", "letta"],
        *,
        url: str | None = None,
        api_key: str | None = None,
        embedder: Embedder | None = None,
        executor_max_workers: int | None = None,    # NEW vs Memory; only used by threadwrap
        **provider_kwargs: Any,
    ) -> None: ...

    async def add(self, content: str, user_id: str, *, tags: Sequence[str] | None = None,
                  scope: str | None = None, metadata: Mapping[str, Any] | None = None) -> MemoryRecord: ...
    async def get(self, id: str, user_id: str) -> MemoryRecord: ...
    async def search(self, query: str, user_id: str, *, limit: int = 10,
                     scope: str | None = None) -> list[SearchHit]: ...
    async def list(self, user_id: str, *, scope: str | None = None,
                   cursor: str | None = None, limit: int = 50) -> MemoryPage: ...
    async def update(self, id: str, user_id: str, *, content: str | None = None,
                     tags: Sequence[str] | None = None,
                     metadata: Mapping[str, Any] | None = None) -> MemoryRecord: ...
    async def delete(self, id: str, user_id: str) -> None: ...
    async def context(self, query: str, user_id: str, *, limit: int = 5) -> ContextBlock: ...
    async def capabilities(self) -> Capabilities: ...
    async def wait_for_ingest(self, ids: Sequence[str], user_id: str,
                              *, timeout: float | None = None) -> None: ...

    async def close(self) -> None: ...
    async def __aenter__(self) -> "AsyncMemory": ...
    async def __aexit__(self, *exc) -> None: ...
```

### Invariants

| ID | Invariant |
|---|---|
| AM-INV-1 | All return types are **identical** to the corresponding `Memory.*` return types. No new types are introduced. |
| AM-INV-2 | All raised exception classes are **identical** to `Memory.*` exceptions. |
| AM-INV-3 | `__init__` performs ZERO blocking I/O. Connection pool / HTTP client / threadpool creation MAY happen lazily on first verb call OR on `__aenter__`, but never in `__init__`. |
| AM-INV-4 | `close()` is idempotent. The second and subsequent calls are no-ops. |
| AM-INV-5 | After `close()`, every verb call MUST raise `RuntimeError("AsyncMemory is closed")` BEFORE touching any backend resource. |
| AM-INV-6 | Cross-event-loop misuse is detected: if `_loop_id` captured at first-use differs from `id(asyncio.get_running_loop())`, raise `RuntimeError("AsyncMemory is bound to a different event loop")`. |
| AM-INV-7 | The `executor_max_workers` kwarg is silently ignored by `async_postgres` and `async_passthrough` adapters (they don't use a threadpool). It MUST NOT raise. |

### State

- `_adapter: AsyncBaseAdapter` — the underlying async adapter
- `_closed: bool = False`
- `_loop_id: int | None = None` — set on first verb call
- `_executor: ThreadPoolExecutor | None = None` — only created by threadwrap adapter; owned by `AsyncMemory` so `close()` can shut it down

### Lifecycle

```
__init__()    →  _closed=False, _adapter selected by provider, no I/O
first verb    →  capture _loop_id; ensure adapter pool/client initialized
  ⋮
close()       →  await adapter.close(); shut down executor; _closed=True
```

---

## 2. `AsyncBaseAdapter` (PR-A)

Internal protocol implemented by every async adapter.

### Protocol

```python
class AsyncBaseAdapter(Protocol):
    async def add(self, content: str, user_id: str, **kw) -> MemoryRecord: ...
    async def get(self, id: str, user_id: str) -> MemoryRecord: ...
    async def search(self, query: str, user_id: str, *, limit: int) -> list[SearchHit]: ...
    async def list(self, user_id: str, *, cursor: str | None, limit: int) -> MemoryPage: ...
    async def update(self, id: str, user_id: str, **kw) -> MemoryRecord: ...
    async def delete(self, id: str, user_id: str) -> None: ...
    async def context(self, query: str, user_id: str, *, limit: int) -> ContextBlock: ...
    async def capabilities(self) -> Capabilities: ...
    async def wait_for_ingest(self, ids: Sequence[str], user_id: str,
                              *, timeout: float | None) -> None: ...
    async def close(self) -> None: ...
```

### Concrete implementations (PR-A)

| Class | Module | Cancellation tier (R3) | Notes |
|---|---|---|---|
| `AsyncPostgresAdapter` | `adapters/async_postgres.py` | Native | Uses `asyncpg.create_pool`; shares SQL strings with sync `postgres.py` via new `_postgres_sql.py` module. |
| `AsyncPassthroughAdapter` | `adapters/async_passthrough.py` | Native | Uses `httpx.AsyncClient`; transport defaults match sync `passthrough.py` (timeout, retries, headers). |
| `AsyncThreadwrapAdapter` | `adapters/async_threadwrap.py` | Best-effort | Constructed with a sync adapter instance + an executor; every method is `await asyncio.get_running_loop().run_in_executor(self._executor, sync_method, *args)`. |

`AsyncMemory.__init__` selects:

```python
sync_only = {"mem0", "supermemory", "letta"}
if provider == "postgres":     adapter = AsyncPostgresAdapter(...)
elif provider == "passthrough": adapter = AsyncPassthroughAdapter(...)
elif provider in sync_only:    adapter = AsyncThreadwrapAdapter(make_sync_adapter(provider, ...), executor)
```

---

## 3. `OmpServerConfig` (PR-B)

Dataclass capturing CLI args + env defaults.

```python
@dataclass(frozen=True)
class OmpServerConfig:
    provider: str                              # OMP_PROVIDER
    host: str = "127.0.0.1"                    # OMP_HOST
    port: int = 8080                           # OMP_PORT
    max_request_bytes: int = 1024 * 1024       # OMP_MAX_REQUEST_BYTES, 1 MiB
    cors_origins: tuple[str, ...] = ()         # OMP_CORS_ORIGINS (comma split)
    log_level: str = "info"                    # OMP_LOG_LEVEL

    # Provider-specific
    postgres_url: str | None = None            # OMP_POSTGRES_URL or PG_URL
    mem0_api_key: str | None = None            # MEM0_API_KEY
    supermemory_api_key: str | None = None     # SUPERMEMORY_API_KEY
    letta_api_key: str | None = None           # LETTA_API_KEY
```

### Invariants

| ID | Invariant |
|---|---|
| CFG-INV-1 | `port` MUST be in `1..65535` else `ValueError` at construction. |
| CFG-INV-2 | `max_request_bytes` MUST be in `1024..104_857_600` (1 KiB..100 MiB). |
| CFG-INV-3 | If `provider == "postgres"`, `postgres_url` MUST be non-empty. |
| CFG-INV-4 | If `provider in {"mem0","supermemory","letta"}`, the matching API key MUST be non-empty. |

---

## 4. FastAPI app structure (PR-B)

```
openmem.server.app.create_app(config: OmpServerConfig) -> FastAPI
```

`create_app`:
1. Builds the AsyncMemory: `mem = AsyncMemory(provider=config.provider, **resolved_kwargs)`.
2. Registers it as an app-state singleton: `app.state.memory = mem`.
3. Mounts routers from `routes.py`.
4. Registers exception handlers from `errors.py`.
5. Adds startup hook to `await mem.__aenter__` (warm pool) and shutdown hook to `await mem.close()`.

### Dependency injection

```python
async def get_memory(request: Request) -> AsyncMemory:
    return request.app.state.memory
```

Used by every route handler via `mem: AsyncMemory = Depends(get_memory)`.

---

## 5. Error envelope (reused from `spec/omp-0.1.openapi.yaml`)

```json
{ "error": { "code": "<enum>", "message": "<str>", "details": { ... } } }
```

Mapping (FR-017):

| Exception class | HTTP status | `code` |
|---|---|---|
| `NotFoundError` | 404 | `not_found` |
| `InvalidRequestError` | 400 | `invalid_request` |
| `UnauthorizedError` | 401 | `unauthorized` |
| `ScopeDeniedError` | 403 | `scope_denied` |
| `RateLimitedError` | 429 | `rate_limited` |
| `UnsupportedCapabilityError` | 405 | `unsupported_capability` |
| `ProviderError(code="ingestion_timeout")` | 504 | `ingestion_timeout` |
| `ProviderError` (any other) | 502 | `provider_error` |
| `Exception` (unhandled) | 500 | `internal_error` |

The `details` field MAY be omitted on success or sanitized to omit `user_id`, headers, and any field beginning with `api_`.

---

## 6. Logging contract (PR-B)

Every request log line contains:

```
<iso8601> <level> <method> <path> <status> <latency_ms> req=<request_id>
```

Forbidden (per FR-020): request body, response body, `user_id`, `api_key`, `Authorization` header, any field whose name matches `(?i)password|secret|token|key`.

Implementation: a custom `LoggingMiddleware` that constructs the line from `Request.method`, `Request.url.path`, `response.status_code`, the elapsed time, and a per-request UUID4. The middleware MUST NOT touch `request.body()`.

---

## 7. Health endpoint (PR-B, FR-019)

```
GET /healthz
  200 OK   {"status": "ok"}                                                     # adapter reachable
  503      {"error": {"code": "provider_unavailable", "message": "<detail>"}}   # not reachable
```

Implementation:
- For `postgres`: `await pool.acquire(timeout=1.0)` then immediate release. SELECT 1 NOT executed (avoids waking pgvector hot path).
- For `passthrough`: `await client.head(base_url, timeout=2.0)`.
- For mem0/supermemory/letta: returns `200 OK` unconditionally — health-checking paid endpoints would burn quota. Documented in the contract.

---

## 8. Conformance test fixtures (PR-A & PR-B)

Reuse the existing `tests/conftest.py` patterns:
- `_make_async_memory(provider, **kw)` factory mirroring the existing `_make_memory`.
- `pytest_asyncio.fixture` for per-test `AsyncMemory` instances with auto-cleanup via `await mem.close()` in finalizer.
- Live-mode finalizer tracks created ids and `await mem.delete(id)` at teardown (FR-119 from M2.1, applied to async).
