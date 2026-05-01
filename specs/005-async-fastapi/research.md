# Phase 0 Research — M3.2 Async facade + FastAPI server

This document records the technical decisions made during research, with rationale and rejected alternatives, before any code is written.

---

## R1 — Async PostgreSQL driver: `asyncpg` vs `psycopg[async]`

**Decision**: **`asyncpg>=0.29`**.

**Rationale**:
- Pure-async, no thread-shim. Faster (≈3× sync `psycopg` on small statements per published benchmarks) which directly serves SC-001 (≥10× sync RPS).
- Native cancellation: `asyncio.CancelledError` raised at any `await` point inside `asyncpg.Connection.fetch()` calls the underlying `pg_cancel_backend` flow, satisfying SC-003 directly with no extra plumbing.
- Mature pool (`asyncpg.create_pool`) with sane defaults (`min_size=10, max_size=10`) — drop-in for our existing connection-management patterns.
- Already widely used in the FastAPI ecosystem; documentation & community examples are abundant.

**Alternatives considered**:
- **`psycopg[async]>=3.2`**: Modern, official, supports both sync + async with the same API. Rejected because (a) on small queries it benchmarks slower than `asyncpg`, (b) cancellation propagation requires `cancel_safe=True` mode and is less battle-tested, (c) we already use sync `psycopg` for the existing adapter and unifying drivers would *increase* code paths, not reduce them.
- **`aiopg`**: Older, semi-maintained shim over `psycopg2`. Rejected — effectively obsolete.
- **Wrap sync `psycopg` via `asyncio.to_thread`**: Defeats the entire purpose of `AsyncMemory` for the reference adapter. Rejected.

**Implication for plan**: New runtime dep `asyncpg>=0.29` under `openmem[async]` extra. The async postgres adapter (`adapters/async_postgres.py`) is a fresh implementation, NOT a wrapper around `adapters/postgres.py`. Schema and SQL strings are factored into a shared `_postgres_sql.py` module that both sync and async adapters import.

---

## R2 — Async HTTP client for the passthrough adapter

**Decision**: **`httpx.AsyncClient`** (already a dep; we just use the async sibling of the sync `httpx.Client` already in use).

**Rationale**:
- Same library, same TLS stack, same proxy handling, same auth, same timeout semantics as the existing sync passthrough — so behavioral parity is essentially free.
- Native `asyncio.CancelledError` support: cancelling a `await client.post(...)` aborts the underlying socket immediately.
- Connection pool reuse is automatic; one `AsyncClient` per `AsyncMemory` instance.

**Alternatives considered**:
- **`aiohttp`**: Faster on large payloads but introduces a second HTTP library. Rejected — duplication risk is not justified by the marginal perf gain.
- **`urllib3.HTTPConnectionPool`**: Sync-only; would force threadpool-wrapping. Rejected.

**Implication**: No new dep. We bump the lower bound on `httpx` to `>=0.27` for stable `AsyncClient` semantics under structured concurrency. Sync passthrough adapter unchanged.

---

## R3 — Cancellation propagation contract

**Decision**: **Three-tier cancellation contract** explicit in the `AsyncBaseAdapter` docstring:

| Tier | Adapters | Behavior on `await` cancellation |
|---|---|---|
| **Native** | `async_postgres`, `async_passthrough` | Backend operation aborted at driver level. Connection/socket released within ≤500 ms. `asyncio.CancelledError` re-raised. |
| **Cooperative** | (future: any adapter implementing `cancel_token` protocol) | Reserved for adapters that expose an explicit cancel hook. None at v1. |
| **Best-effort** | `async_threadwrap` (mem0, supermemory, letta) | Awaiter receives `CancelledError` immediately. The worker thread completes its in-flight call and discards the result. The backend MAY have observable side-effects (e.g. mem0 row created). |

**Rationale**:
- Honors the user's explicit ask: "propagate cancellation and abort where supported".
- The three-tier model makes it **impossible for a user to be confused** about whether their cancel actually stopped backend work — it's documented per provider.
- For threadpool-wrapped adapters, the worker thread MUST complete its call (Python has no safe way to kill a thread); we discard the result rather than leak it.

**Alternatives considered**:
- **Propagate cancellation everywhere or nowhere**: Rejected — over-promising on threadpool-wrapped adapters would mislead users into thinking their data wasn't written when it actually was.
- **Refuse to wrap sync adapters**: Rejected — would block 3 of 5 providers from `AsyncMemory`, defeating the milestone.

**Implementation sketch**:
- `async_postgres.py`: `await self._pool.acquire()` and `await conn.fetch(...)` are both natively cancellable; `asyncpg` handles connection release in `__aexit__`.
- `async_passthrough.py`: `httpx.AsyncClient.request()` is natively cancellable.
- `async_threadwrap.py`: `await asyncio.to_thread(self._sync.add, ...)`. When the awaiter cancels, `asyncio.to_thread` raises `CancelledError` to the awaiter while the future on the worker thread continues; we attach a no-op `add_done_callback` that logs at DEBUG when the wrapped call eventually completes after cancellation (visibility for ops).

---

## R4 — Threadpool sizing for the wrapper adapter

**Decision**: **Per-`AsyncMemory` private `ThreadPoolExecutor`** with `max_workers = min(32, (os.cpu_count() or 1) + 4)` (matches Python's stdlib `ThreadPoolExecutor` default), overridable via constructor kwarg `executor_max_workers=N`.

**Rationale**:
- Using `asyncio.get_running_loop().run_in_executor(None, ...)` would share the global default executor across the whole process. For a server hosting multiple `AsyncMemory` instances (or a user mixing several providers), this creates non-obvious contention.
- A private pool isolates failure modes and makes `AsyncMemory.close()` deterministic (we know exactly what to shut down).
- Stdlib default is well-tuned for the I/O-bound case we have here.

**Rejected**: A single shared pool. Rejected for isolation reasons above. Reserved as a later optimization if needed.

---

## R5 — Packaging extras layout

**Decision**: **Two opt-in extras**:

```toml
[project.optional-dependencies]
async = ["asyncpg>=0.29", "httpx>=0.27"]
server = ["openmem[async]", "fastapi>=0.115", "uvicorn[standard]>=0.30"]
```

**Rationale**:
- Bare `pip install openmem` keeps the existing footprint exactly as today (FR-025, SC-007).
- `pip install openmem[async]` adds only the two async deps; no FastAPI bloat for users who just want `AsyncMemory`.
- `pip install openmem[server]` recursively pulls `[async]` since the server requires it.
- `from openmem import AsyncMemory` and `from openmem.server import app` BOTH guard the import with a clear `ImportError` listing the right `pip install` command (FR-026).

**Rejected**:
- Bundling everything in the base install. Rejected — violates SC-007 and forces FastAPI on users who don't need it.
- Separate distribution packages (`openmem-async`, `openmem-server`). Rejected — releases would have to be co-versioned, which is more painful than extras for the same outcome.

---

## R6 — How `AsyncMemory` is exposed from `openmem.__init__`

**Decision**: **Lazy import via `__getattr__`** (PEP 562):

```python
# openmem/__init__.py
def __getattr__(name: str):
    if name == "AsyncMemory":
        try:
            from .async_memory import AsyncMemory
        except ImportError as exc:
            raise ImportError(
                "AsyncMemory requires the [async] extra. "
                "Install with: pip install 'openmem[async]'"
            ) from exc
        return AsyncMemory
    raise AttributeError(f"module 'openmem' has no attribute {name!r}")
```

**Rationale**:
- A bare `pip install openmem` (no `asyncpg`/`httpx[async]`) MUST not crash on `import openmem`. Lazy `__getattr__` defers the import cost & failure to `from openmem import AsyncMemory`.
- Static analysis tools and `__all__` still list `AsyncMemory` so editors autocomplete it.

**Rejected**:
- Top-level `from .async_memory import AsyncMemory` at module load. Rejected — would require `[async]` extras for *every* installation, breaking SC-007.
- Making users `from openmem.async_memory import AsyncMemory` directly. Rejected — uglier ergonomics, asymmetric with sync `Memory`.

---

## R7 — FastAPI app construction & OpenAPI mounting

**Decision**: **Hand-write FastAPI routes that mirror `spec/omp-0.1.openapi.yaml`** rather than auto-generating them via `datamodel-code-generator` or `fastapi-codegen`.

**Rationale**:
- The spec is small (~10 routes) and stable. Codegen adds a build-time dependency, a generated-file review burden, and obscures error-mapping logic.
- We already have hand-maintained pydantic models in `openmem/types.py` that mirror the spec. Reusing them in FastAPI route signatures gives automatic request validation + OpenAPI docs at `/docs` for free.
- A separate test (`test_server_openapi_conformance.py`) validates EVERY response against the spec at runtime — this is the actual conformance gate, not codegen.

**Rejected**:
- `fastapi-codegen` from the spec. Rejected — generated code would conflict with our existing pydantic models and add a generation step to CI.
- Mounting the entire spec as a static `openapi.json` with `app.openapi = lambda: yaml.load(...)`. Considered as an enhancement; defer to PR-B implementation.

---

## R8 — How the server enforces cancellation on client disconnect

**Decision**: **Use ASGI's `request.is_disconnected()` helper plus `anyio.create_task_group` per request** (FastAPI/Starlette idiom).

```python
# pseudocode in routes.py
@router.get("/v1/memories/{id}")
async def get_memory(id: str, mem: AsyncMemory = Depends(get_memory_dep)):
    return await mem.get(id, user_id=...)   # Starlette auto-cancels this on client disconnect
```

Starlette already cancels the request task when the client disconnects (since 0.36+). Because our adapter calls are themselves cancellable (R3), the cancellation propagates through to `asyncpg`/`httpx` and aborts the in-flight backend operation. **No explicit `is_disconnected()` polling required** — the cancellation is structural.

**Rationale**: Free win from using the framework correctly. Less code = fewer bugs.

**Rejected**: Polling `is_disconnected()` on a timer. Rejected — adds latency and CPU overhead for no gain.

---

## R9 — Test framework for async tests

**Decision**: **`pytest-asyncio>=0.24` with `asyncio_mode = "auto"`** in `pyproject.toml`.

**Rationale**:
- Auto-mode means we just write `async def test_foo(): ...` — no `@pytest.mark.asyncio` boilerplate.
- The 0.24+ release line is stable on Python 3.11+ and integrates cleanly with our existing pytest 9.x.
- `httpx.AsyncClient` is the recommended FastAPI test client (replaces deprecated `TestClient` for async route handlers); it's also already a dep via R2.

**Rejected**:
- `anyio` test mode. Considered — would let us test trio + asyncio. Rejected — we don't support trio; sticking to asyncio simplifies the surface.
- `unittest.IsolatedAsyncioTestCase`. Rejected — the rest of our suite is pytest-native.

---

## R10 — Live test gating (postgres + paid providers)

**Decision**: **Reuse the existing M2.1 convention**: tests marked `@pytest.mark.live` auto-skip unless `OMP_LIVE=1` and the relevant `*_API_KEY` env vars are set. Async live tests reuse the same finalizer pattern that tracks created memory ids and deletes them at teardown.

**Rationale**: One convention for both sync and async live tests means no surprise for contributors. The eval kit's env-aware factory (`_default_factory` in `runner.py`) already proves the pattern works.

**Rejected**: A separate `OMP_ASYNC_LIVE` env. Rejected — needless duplication; if `OMP_LIVE=1` is set the async live tests should run too.

---

## Summary of decisions

| ID | Topic | Decision |
|---|---|---|
| R1 | Async postgres driver | `asyncpg>=0.29` |
| R2 | Async HTTP client | `httpx.AsyncClient` (existing dep, bump to `>=0.27`) |
| R3 | Cancellation contract | Three-tier (native / cooperative / best-effort) |
| R4 | Threadpool sizing | Per-instance `ThreadPoolExecutor`, stdlib default size |
| R5 | Packaging | Two extras: `[async]` and `[server]` |
| R6 | `AsyncMemory` exposure | Lazy `__getattr__` in `openmem.__init__` |
| R7 | Server route construction | Hand-written FastAPI routes over existing pydantic models |
| R8 | Server-side cancellation | Structural via Starlette task cancellation |
| R9 | Test framework | `pytest-asyncio>=0.24`, auto-mode |
| R10 | Live tests | Reuse `OMP_LIVE=1` + `*_API_KEY` convention |

**No `[NEEDS CLARIFICATION]` remains.** All decisions are committed to in the plan. Implementation may revisit any decision **only** by filing a follow-up question in `tasks.md` and updating this document.
