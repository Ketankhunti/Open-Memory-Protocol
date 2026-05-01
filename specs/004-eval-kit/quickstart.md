# Quickstart — `openmem-eval`

> Manually-invoked benchmark harness. Never runs in CI.

## Install

```powershell
pip install -e "./sdk-python[dev]"
```

`openmem-eval` is now on your PATH.

## See it work without spending anything

```powershell
openmem-eval                       # default: postgres-only, dry-run
openmem-eval --providers postgres,mem0,supermemory,letta --dry-run
```

The second command prints a cost estimate for a full four-provider live run.
No keys required, no network calls made.

## Run against local Postgres only (free, ~30s)

Bring up the test postgres container that the existing live tests use:

```powershell
docker run -d --rm -p 5432:5432 -e POSTGRES_PASSWORD=postgres `
  -e POSTGRES_DB=omp_test pgvector/pgvector:pg16
$env:OMP_POSTGRES_URL = "postgresql://postgres:postgres@localhost:5432/omp_test"

openmem-eval --providers postgres --live --sample 10
```

You will get an `eval-report.md` written to the current directory and a
single-row comparison table on stdout.

## Run against all four providers (live, ~5 min, ~$0.30)

Set the env vars you already use for the live test suite:

```powershell
$env:MEM0_API_KEY        = "..."
$env:SUPERMEMORY_API_KEY = "..."
$env:LETTA_API_KEY       = "..."
$env:OMP_POSTGRES_URL    = "postgresql://..."

openmem-eval --providers postgres,mem0,supermemory,letta --live
```

The harness:
1. Prints the request count + USD estimate.
2. Prompts `Continue? [y/N]:` if the estimate exceeds $1.00.
3. Ingests the bundled dataset into each provider under `user_id=eval-{run_id}`.
4. Waits for ingest to complete (calls `adapter.wait_for_ingest`).
5. Runs all 20 queries against each provider, capturing top-k + latency.
6. Computes recall@1/5/10, MRR, p50/p95 latency.
7. Writes `eval-report.md` with the comparison table and a per-provider
   trace section for any failed queries.

## Re-running cheaply

If you just want to tweak the scorer or re-format the report and don't
need fresh ingest numbers:

```powershell
openmem-eval --providers postgres,mem0 --live --use-cache
```

The cache lives at `~/.cache/openmem/eval/{dataset_hash}/`. It is
`fact_id → memory.id` only; no content is cached.

## Cleanup

To remove every memory the harness created in your providers:

```powershell
openmem-eval --providers postgres,mem0,supermemory,letta --live --cleanup
```

The most recent `run_id` is read from the cache; each `memory.id` is
deleted via `Memory.delete()`. Failures are warnings.

## Troubleshooting

- **"missing $MEM0_API_KEY"** — set the env var or drop that provider
  from `--providers`.
- **"Continue? [y/N]:"** — answer `y` or pass `--yes`.
- **Suspicious recall < 0.1** — verify the provider's index is populated
  (the report flags this); often a sign that ingest hasn't completed.
- **Ingest timeout on mem0** — first run pays the ~30s/fact cost.
  Subsequent runs with `--use-cache` skip this.

## What the report looks like

```
# OMP Eval Report

| provider    | recall@1 | recall@5 | MRR  | ingest p50/p95 (ms) | search p50/p95 (ms) | errors |
|-------------|----------|----------|------|---------------------|---------------------|--------|
| postgres    | 0.85     | 0.95     | 0.91 | 12 / 31             | 18 / 47             | 0      |
| mem0        | 0.80     | 0.90     | 0.87 | 1840 / 2950         | 320 / 540           | 0      |
| supermemory | 0.75     | 0.95     | 0.85 | 4200 / 5800         | 280 / 410           | 0      |
| letta       | 0.70     | 0.85     | 0.78 | 5100 / 6900         | 1100 / 1800         | 1      |

Dataset: default-a1b2c3d4e5f6  (50 facts, 20 queries)
Run: 2026-05-01T12:34:56Z   OMP 0.4.0
```

This is what you copy into the release notes.
