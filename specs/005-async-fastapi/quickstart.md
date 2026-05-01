# Quickstart — M3.2 Async facade + FastAPI server

A short walkthrough of both deliverables once they ship. Use this to validate the implementation post-merge.

---

## Install

```bash
# Bare install — sync Memory only (existing behavior, unchanged)
pip install openmem

# AsyncMemory facade
pip install 'openmem[async]'

# AsyncMemory + FastAPI HTTP server
pip install 'openmem[server]'
```

Verify:

```python
from openmem import Memory          # always works
from openmem import AsyncMemory     # requires [async] extra
```

---

## PR-A — `AsyncMemory`

### 1. Single-call usage

```python
import asyncio
from openmem import AsyncMemory

async def main():
    async with AsyncMemory(provider="postgres", url="postgresql://...") as mem:
        rec = await mem.add(content="user prefers pnpm", user_id="u1")
        hits = await mem.search("package manager", "u1", limit=5)
        for h in hits:
            print(h.memory.content)

asyncio.run(main())
```

### 2. Concurrent fan-out (the headline benefit)

```python
async def ingest_many():
    async with AsyncMemory(provider="postgres", url="...") as mem:
        # 100 inserts in parallel — completes in ~single-insert latency
        await asyncio.gather(*[
            mem.add(content=fact, user_id="u1") for fact in 100_facts
        ])
```

### 3. With a sync-only backend (mem0/supermemory/letta)

```python
async with AsyncMemory(provider="mem0", api_key="...") as mem:
    # Internally wrapped in a threadpool — event loop never blocks
    rec = await mem.add(content="hi", user_id="u1")
```

The threadpool size defaults to `min(32, cpu_count + 4)`. Override:

```python
AsyncMemory(provider="mem0", api_key="...", executor_max_workers=4)
```

### 4. Cancellation (postgres + passthrough)

```python
async def cancel_demo():
    async with AsyncMemory(provider="postgres", url="...") as mem:
        task = asyncio.create_task(mem.search("slow query", "u1"))
        await asyncio.sleep(0.1)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            print("cancelled — postgres pool reclaimed within 500ms")
```

For `mem0`/`supermemory`/`letta` (threadwrap), the awaiter sees `CancelledError` immediately but the worker thread completes its in-flight call in the background. The backend MAY have observable side-effects.

### 5. Sync `Memory` is unchanged

```python
from openmem import Memory          # zero changes from M3.1
mem = Memory(provider="postgres", url="...")
mem.add(content="hi", user_id="u1") # blocks the thread, exactly as before
```

---

## PR-B — `omp-server`

### 1. Boot the server

```bash
# Postgres-backed (recommended for local dev)
export OMP_POSTGRES_URL="postgresql://postgres:postgres@localhost:5432/postgres"
omp-server --provider postgres --port 8080

# Output:
# omp-server: serving postgres at http://127.0.0.1:8080
```

Or via flags only:

```bash
omp-server --provider postgres --url postgresql://... --host 0.0.0.0 --port 8080
```

For mem0:

```bash
export MEM0_API_KEY="mem0-..."
omp-server --provider mem0 --port 8080
```

### 2. Smoke test

```bash
# Health
curl http://localhost:8080/healthz
# {"status":"ok"}

# Add
curl -X POST http://localhost:8080/v1/memories \
  -H "Content-Type: application/json" \
  -d '{"content":"user prefers pnpm","user_id":"u1"}'
# 201 Created
# {"id":"...","content":"user prefers pnpm","user_id":"u1","created_at":"...","tags":[],"metadata":{}}

# Search
curl -X POST http://localhost:8080/v1/search \
  -H "Content-Type: application/json" \
  -d '{"query":"package manager","user_id":"u1","limit":5}'
# 200 OK
# [{"memory":{"id":"...","content":"...","user_id":"u1",...},"score":0.83}]

# List
curl "http://localhost:8080/v1/memories?limit=10" \
  -H "X-User-Id: u1"

# Capabilities
curl http://localhost:8080/v1/capabilities
# {"omp_version":"0.1","verbs":["add","get","search","list","update","delete","capabilities"],...}

# Delete
curl -X DELETE http://localhost:8080/v1/memories/<id> \
  -H "X-User-Id: u1"
# 204 No Content
```

### 3. Error cases

```bash
# Missing user_id → 400 invalid_request
curl -X POST http://localhost:8080/v1/memories \
  -H "Content-Type: application/json" \
  -d '{"content":"hi"}'
# {"error":{"code":"invalid_request","message":"user_id is required"}}

# Unknown id → 404 not_found
curl http://localhost:8080/v1/memories/does-not-exist -H "X-User-Id: u1"
# {"error":{"code":"not_found","message":"..."}}

# Body too large → 413
curl -X POST http://localhost:8080/v1/memories \
  -H "Content-Type: application/json" \
  --data-binary @huge.json
# {"error":{"code":"payload_too_large","message":"max 1048576 bytes"}}
```

### 4. CORS (opt-in)

```bash
omp-server --provider postgres --url ... \
  --cors-origins "https://app.example.com,https://staging.example.com"
```

By default no CORS middleware is installed (FR-022).

### 5. From an async Python client

```python
import httpx

async with httpx.AsyncClient(base_url="http://localhost:8080") as client:
    r = await client.post("/v1/memories", json={"content":"hi","user_id":"u1"})
    rec = r.json()
    r = await client.post("/v1/search", json={"query":"hi","user_id":"u1"})
    hits = r.json()
```

The same code works against any provider — switch the server's `--provider` flag.

---

## Validation against success criteria

| Success Criterion | How to verify |
|---|---|
| **SC-001** ≥10× sync RPS | Run `tests/server/test_throughput_bench.py::test_postgres_async_vs_sync` (auto-skipped without `OMP_POSTGRES_URL`) |
| **SC-002** Concurrent fan-out | Run example 2 above; assert wall time < 2× single-call latency |
| **SC-003** Cancellation pool-release ≤500 ms | Run `tests/async/test_async_cancellation.py::test_postgres_pool_release` |
| **SC-004** Existing tests unchanged | `pytest sdk-python/tests` — must report `365 passed` (the M3.1 baseline) before counting new async/server tests |
| **SC-005** OpenAPI conformance 100% | Run `tests/server/test_server_openapi_conformance.py` |
| **SC-006** Boot <2 s, first request <100 ms | Run `tests/server/test_server_cli.py::test_boot_time` |
| **SC-007** Bare install preserves sync surface | Run in a fresh venv: `pip install openmem; python -c "from openmem import Memory; from openmem import AsyncMemory"` — second import MUST raise `ImportError` with `pip install 'openmem[async]'` in message |
| **SC-008** `Memory` byte-identical | Run `tests/async/test_async_facade.py::test_memory_signatures_unchanged` |

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `ImportError: AsyncMemory requires the [async] extra` | Bare install | `pip install 'openmem[async]'` |
| `RuntimeError: AsyncMemory is bound to a different event loop` | Reusing the instance across `asyncio.run` calls | Construct a new `AsyncMemory` per loop, or use `async with` to scope it |
| `RuntimeError: AsyncMemory is closed` | Used after `close()` | Construct a new instance |
| `omp-server: missing config: postgres requires --url or OMP_POSTGRES_URL` | No DB URL provided | Set the env var or pass `--url` |
| Server returns `503 provider_unavailable` | Postgres unreachable, pool exhausted, or paid backend down | Check `omp-postgres` container / API keys; check `/healthz` |
| Logs contain `user_id` | **Bug — file an issue.** Logging contract C-LOG-2 forbids it. | Report the offending log line + commit SHA |
