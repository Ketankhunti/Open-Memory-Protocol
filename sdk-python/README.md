# `openmem` — Python SDK

Reference Python implementation of the [Open Memory
Protocol](../spec/OMP-0.1.md) v0.1.

## Install

```powershell
pip install -e .                 # core
pip install -e ".[dev]"          # core + tests
pip install -e ".[openai]"       # core + OpenAIEmbedder
```

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
  [specs/004-eval-kit/quickstart.md](../specs/004-eval-kit/quickstart.md).
