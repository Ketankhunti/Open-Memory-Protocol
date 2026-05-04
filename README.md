# Open Memory Protocol (OMP)

> One API for any AI memory provider.

OMP is an open standard plus reference Python SDK that lets agentic
applications talk to any memory backend — Postgres, Mem0, Supermemory,
Letta, your own — through one stable set of verbs.

## 30-second quickstart

```powershell
docker run --rm -d -p 5432:5432 -e POSTGRES_PASSWORD=postgres pgvector/pgvector:pg16
pip install -e ./sdk-python
$env:PG_URL = "postgresql://postgres:postgres@localhost:5432/postgres"
python examples/01_quickstart.py
```

```python
from openmem import Memory

mem = Memory(provider="postgres", url="postgresql://localhost/omp")
mem.add(content="user prefers pnpm over npm", user_id="kek",
        scope="coding/preferences", tags=["tooling", "nodejs"])

ctx = mem.context("set up a new node project", user_id="kek", token_budget=500)
print(ctx.text)
```

## Provider matrix

| Provider              | Mode             | Status     |
| --------------------- | ---------------- | ---------- |
| Postgres + pgvector   | Native (ref)     | 🟢 Stable  |
| Native OMP endpoints  | Passthrough      | 🟢 Stable  |
| Mem0                  | Translation      | 🟢 Stable  |
| Supermemory           | Translation      | 🟢 Stable  |
| Letta                 | Translation      | 🟢 Stable  |

Live capability advertisement is authoritative — every adapter exposes its real verb set and feature flags via `mem.capabilities()` and `GET /capabilities` on `omp-server`. See [docs/providers.mdx](docs/providers.mdx) for the per-feature matrix.

## Documents

- [SPEC](spec/OMP-0.1.md) — narrative spec
- [OpenAPI](spec/omp-0.1.openapi.yaml) — canonical schema (Constitution Principle I)
- [Constitution](.specify/memory/constitution.md) — non-negotiable engineering principles
- [SDK README](sdk-python/README.md)
- [CHANGELOG](CHANGELOG.md)

## Tooling

- `openmem-eval` — manual benchmark harness comparing recall, MRR, and latency across configured providers. **Never runs in CI.** Default invocation is a dry-run that makes zero network calls. See [specs/004-eval-kit/quickstart.md](specs/004-eval-kit/quickstart.md) for usage and [docs/eval/](docs/eval/README.md) for a sample report and trace from a real postgres run.
- `openmem.AsyncMemory` — async/await-native facade mirroring `openmem.Memory`. Postgres + passthrough adapters use native async clients (asyncpg, httpx); mem0/supermemory/letta are wrapped with a per-instance thread pool. Cancellation propagates within 50 ms on the native tier. Install with `pip install 'openmem[async]'` and see [specs/005-async-fastapi/quickstart.md](specs/005-async-fastapi/quickstart.md) §1–§4.
- `omp-server` — production-shaped FastAPI HTTP server that exposes the full OMP verb set over HTTP using `AsyncMemory` under the hood. Install with `pip install 'openmem[server]'` then boot, e.g.:

  ```sh
  omp-server --provider postgres --url postgresql://user:pass@host:5432/db --port 8080
  ```

  Trusted-network deployment only — auth is deferred to a future release. See [specs/005-async-fastapi/contracts/http-server.md](specs/005-async-fastapi/contracts/http-server.md) for the route table, error-envelope mapping, and CORS / size-limit / health-check contracts.

## HTTP server

```sh
pip install 'openmem[server]'
omp-server --provider postgres --url postgresql://user:pass@host:5432/db
# omp-server: serving postgres at http://127.0.0.1:8080
```

Routes mirror `spec/omp-0.1.openapi.yaml` exactly: `POST/GET/PATCH/DELETE /memories[/{id}]`, `GET /memories/search`, `POST /context`, `GET /audit`, `GET /capabilities`, `GET /healthz`. Every request emits one INFO access log line with no `user_id` and no secret-keyed values.

## License

TBD.
