# OMP Eval Kit — sample outputs

This directory contains **committed sample artifacts** from the
`openmem-eval` benchmarking kit so you can see what a run looks like
without installing or running anything.

## Files

| File | What it is | How it was produced |
|---|---|---|
| [sample-report.md](sample-report.md) | Markdown results table — recall@k, MRR, ingest/search latency percentiles, error count, cost estimate | `openmem-eval --providers postgres --live --yes --cleanup` against a local `pgvector/pgvector:pg16` container |
| [sample-trace.jsonl](sample-trace.jsonl) | One JSON line per verb call: `add` × 50 + `search` × 20 = 70 lines. Payloads are SHA-256 hashed (12-hex prefix) so the trace is safe to share. | Same run as above, written via `--trace` |

## Reproducing locally

```bash
# 1. Start a local pgvector container
docker run -d --name omp-postgres -p 5432:5432 \
  -e POSTGRES_PASSWORD=postgres pgvector/pgvector:pg16

# 2. Point the eval at it and run the bundled 50-fact / 20-query dataset
export OMP_POSTGRES_URL=postgresql://postgres:postgres@localhost:5432/postgres
openmem-eval --providers postgres --live --yes --cleanup \
  --report eval-report.md --trace eval-trace.jsonl
```

Cost: **$0.00** (postgres + offline `FakeEmbedder`). Wall time: ~1s.

## Reading the report

```
| Provider | Status | recall@1 | recall@5 |  MRR  | ingest p50/p95/p99 (ms) | search p50/p95/p99 (ms) | errors | est. cost |
| postgres |   ok   |  0.750   |  0.800   | 0.775 |       8.6/12.4/24.5     |        5.1/6.4/6.5      |   0    |  $0.0000  |
```

- **recall@k** — fraction of queries where any gold fact appears in the top-`k` results.
- **MRR** — Mean Reciprocal Rank across all queries.
- **Latency percentiles** — wall-clock time for each `add`/`search` call, in milliseconds.
- **est. cost** — derived from per-verb pricing in `openmem.eval.cost`. Postgres is $0; paid providers (mem0, supermemory, letta) are estimated **before** the run and gated by a confirmation prompt above `--cost-threshold` (default $1.00).

## Notes on the bundled dataset

- 50 facts × 20 queries; SHA-256 dataset hash `5c2a0d95915a` is recorded in every report so two runs of the same dataset are directly comparable.
- The hash-based offline embedder (`FakeEmbedder`) caps recall@5 around 0.80 because some queries share few literal tokens with their gold facts. Production deployments using `OpenAIEmbedder` typically reach ~0.95+ recall on the same dataset.

## See also

- [Spec & design](../../specs/004-eval-kit/) — full requirements, plan, tasks
- [Quickstart](../../specs/004-eval-kit/quickstart.md) — full CLI reference and exit-code table
- [Source](../../sdk-python/openmem/eval/) — runner, scorer, trace, report, cost, cleanup
