# `openmem` — Python SDK

Reference Python implementation of the [Open Memory
Protocol](../spec/OMP-0.1.md) v0.1.

## Install

```powershell
pip install -e .                 # core
pip install -e ".[dev]"          # core + tests
pip install -e ".[openai]"       # core + OpenAIEmbedder
pip install -e ".[async]"        # core + AsyncMemory (asyncpg)
```

## Async usage

`openmem.AsyncMemory` is the async/await-native mirror of
`openmem.Memory`. Method names, parameters, and error semantics match
the sync class — the only difference is that every verb is awaitable.
Postgres + passthrough run on native async clients (asyncpg / httpx);
mem0, supermemory, and letta are wrapped with a per-instance
`ThreadPoolExecutor`.

```python
import asyncio
from openmem import AsyncMemory   # requires: pip install 'openmem[async]'

async def main():
    async with AsyncMemory(provider="postgres",
                           url="postgresql://postgres:postgres@localhost:5432/postgres") as mem:
        rec = await mem.add(content="user prefers dark mode", user_id="u1")
        hits = await mem.search("dark mode", user_id="u1")
        print(hits[0].memory.content)

asyncio.run(main())
```

Cancellation propagates within 50 ms on the native tier (postgres,
passthrough); the threadwrap tier returns immediately to the awaiter
while the worker thread completes in the background. See
[../specs/005-async-fastapi/quickstart.md](../specs/005-async-fastapi/quickstart.md)
for the full contract.

## Environment variables

| Var              | Purpose                                                   |
| ---------------- | --------------------------------------------------------- |
| `PG_URL`         | Postgres + pgvector connection string                     |
| `OPENAI_API_KEY` | Required only if you instantiate `OpenAIEmbedder`         |

## Supported providers (M1)

| `provider=`       | Adapter             | Default embedder       |
| ----------------- | ------------------- | ---------------------- |
| `"postgres"`      | `PostgresAdapter`   | `FakeEmbedder` (offline) |
| (any) + `base_url=` | `PassthroughAdapter` (M2) | n/a |

## Run the test suite

```powershell
pip install -e ".[dev]"
# Either set PG_URL to a running pgvector instance...
$env:PG_URL = "postgresql://postgres:postgres@localhost:5432/postgres"
# ... or rely on testcontainers + Docker.
pytest tests -q
```

Coverage gates (Constitution Principle II):
- Overall: `--cov-fail-under=85`
- `openmem.adapters.postgres`: `--cov-fail-under=90` (CI step in
  `.github/workflows/ci.yml`)

## Add a new adapter

1. Subclass `openmem.adapters.base.BaseAdapter`. Implement every verb.
2. Append your fixture to the `adapter` parametrization in
   [tests/conftest.py](tests/conftest.py).
3. Run `pytest tests -q`. Green = conformant.

That's the contract. Per Constitution Principle II
([NON-NEGOTIABLE](../.specify/memory/constitution.md)), if the suite is
green your adapter is a drop-in replacement for any other.

## Tooling

- `omp-validate-spec` — validate the OpenAPI spec against the 3.x meta-schema.
- `openmem-eval` — manual benchmark harness comparing recall, MRR, and
  latency across configured providers. **Never runs in CI.** Default
  invocation makes zero network calls. See
  [specs/004-eval-kit/quickstart.md](../specs/004-eval-kit/quickstart.md)
  for usage and [docs/eval/](../docs/eval/README.md) for a committed
  sample report + trace from a real postgres run.
