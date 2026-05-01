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

| Provider              | Mode            | Status        |
| --------------------- | --------------- | ------------- |
| Postgres + pgvector   | Native (ref)    | 🟢 Ready (M1) |
| Native OMP endpoints  | Passthrough     | 🟡 Stub (M2)  |
| Mem0                  | Translation     | 🔜 M2         |
| Supermemory           | Translation     | 🔜 M2         |
| Letta                 | Translation     | 🔜 M2         |

## Documents

- [SPEC](spec/OMP-0.1.md) — narrative spec
- [OpenAPI](spec/omp-0.1.openapi.yaml) — canonical schema (Constitution Principle I)
- [Constitution](.specify/memory/constitution.md) — non-negotiable engineering principles
- [SDK README](sdk-python/README.md)
- [CHANGELOG](CHANGELOG.md)

## Tooling

- `openmem-eval` — manual benchmark harness comparing recall, MRR, and latency across configured providers. **Never runs in CI.** Default invocation is a dry-run that makes zero network calls. See [specs/004-eval-kit/quickstart.md](specs/004-eval-kit/quickstart.md) for usage and [docs/eval/](docs/eval/README.md) for a sample report and trace from a real postgres run.

## License

TBD.
