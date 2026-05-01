# CLI Contract — `openmem-eval`

This document is the canonical contract for the eval CLI. Tests under
`sdk-python/tests/eval/test_cli_*.py` validate every clause here.

## Invocation

Both forms are equivalent:

```
openmem-eval [FLAGS]
python -m openmem.eval [FLAGS]
```

## Flags

| Flag | Type | Default | Required | Description |
|---|---|---|---|---|
| `--providers` | comma-separated list | `postgres` | no | Provider names to run. Aliases like `postgres:hnsw` allowed. |
| `--live` | bool flag | false | no | Make real API calls. Mutually exclusive with `--dry-run`. |
| `--dry-run` | bool flag | true | no | Print cost estimate, make no network calls. |
| `--report` | path | `eval-report.md` | no | Markdown output path. |
| `--trace` | path | `eval-trace.jsonl` | no | JSONL trace output path (live runs only). |
| `--sample` | int | none (= all) | no | Take first N queries by stable order. |
| `--cost-threshold` | float | `1.00` | no | USD; above this, prompt for confirmation. |
| `--yes` | bool flag | false | no | Skip the confirmation prompt. |
| `--cleanup` | bool flag | false | no | Delete every memory created by this run before exiting. |
| `--dataset` | path | bundled default | no | Override bundled dataset (advanced use). |
| `-v` / `--verbose` | bool flag | false | no | Echo per-call progress to stderr. |
| `-h` / `--help` | bool flag | — | no | Print help and exit 0. |
| `--version` | bool flag | — | no | Print SDK + dataset version and exit 0. |

### Mutual exclusion

- `--live` and `--dry-run` are mutually exclusive. Default behaviour
  when neither is set: `--dry-run`.

## Exit Codes

| Code | Meaning |
|---|---|
| 0 | Success. Includes runs where some providers were skipped or failed (the report records the outcome). |
| 1 | No providers were runnable (all skipped due to missing keys). |
| 2 | Bad invocation (unknown provider, malformed flags, missing dataset file). |
| 3 | Confirmation refused (live mode above cost threshold without `--yes` and no TTY). |
| 4 | Internal error in the harness (bug). |

Non-zero exits MUST print a single line to stderr explaining the cause.

## Standard Output (stdout)

Stdout is reserved for the human-readable summary printed at the end of a run:

```
OMP Eval Report  (run_id=<uuid>)
================================
Dataset: default-<dataset_hash>  (50 facts, 20 queries)
Mode: live   Providers: postgres, mem0
Wall-clock: 142.3 s   Estimated cost: $0.04

provider     recall@1 recall@5 recall@10  MRR    ingest p50/p95   search p50/p95   errors
postgres     0.85     0.95     0.95       0.91   12 / 31 ms       18 / 47 ms       0
mem0         0.80     0.90     0.95       0.87   1840 / 2950 ms   320 / 540 ms     1

Report written: eval-report.md
Trace written:  eval-trace.jsonl
```

In `--dry-run` mode the table is replaced by a cost-estimate table:

```
DRY RUN — no live API calls made.

provider     adds   searches   waits   est. cost
postgres     50     20         50      $0.00
mem0         50     20         50      $0.012  (free tier may cover)
TOTAL                                  $0.012
```

## Standard Error (stderr)

stderr is for progress and warnings:

- `[skip] supermemory: missing $SUPERMEMORY_API_KEY`
- `[warn] cost model unknown for (foo, bar); assuming $0`
- `[verbose] mem0/add f-0001 -> mem_abc latency=142ms` (only with `-v`)

## Confirmation Prompt

When `--live` is set, estimated cost > `--cost-threshold`, `--yes` is not
passed, and stdin is a TTY:

```
This run will make ~360 API calls across 4 providers.
Estimated cost: $0.32 USD.
Continue? [y/N]: 
```

A response other than `y` or `Y` aborts with exit 3.

## Files Written

| Path | When | Content |
|---|---|---|
| `eval-report.md` (or `--report`) | Always | Markdown report (see `data-model.md` § Report). |
| `eval-trace.jsonl` (or `--trace`) | Live runs only | One JSON object per live API call. |

## Determinism Guarantees

- Same `--providers`, same `--sample`, same dataset content ⇒ identical
  recall@k and MRR (latency naturally varies).
- `--sample N` selects the same N queries every run (stable hash order).
- `--dataset PATH` makes the dataset_hash a function purely of file
  content, so external datasets remain reproducible.

## Non-Goals

- The CLI does NOT support concurrent provider execution in v1
  (asyncio.gather is a P3 future enhancement noted in the spec
  assumptions).
- The CLI does NOT support custom scorers in v1.
- The CLI does NOT install in the user's PATH automatically — it is
  registered via `[project.scripts]` and becomes available after
  `pip install openmem`.
