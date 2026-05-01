# Implementation Plan: M3.1 Eval Kit (`openmem-eval`)

**Branch**: `004-eval-kit` | **Date**: 2026-05-01 | **Spec**: [spec.md](spec.md)
**Input**: Feature specification from `/specs/004-eval-kit/spec.md`

## Summary

Ship `openmem-eval` — a CLI benchmark harness that ingests a bundled dataset
into one or more OMP adapters, executes a fixed query set, scores recall@k /
MRR / latency, and emits a Markdown comparison report. Live API calls are
opt-in (`--live`); the default `--dry-run` mode prints a per-provider request
count and USD cost estimate so accidental quota burn is impossible. Cost
ceiling for a full four-provider live run: USD $0.50.

The harness is a thin orchestrator over the existing `Memory` facade — it
introduces no new adapter surface area and adds no runtime dependency on any
vendor. It is **not** wired into CI; it is a manually invoked maintainer
tool used to produce release-notes comparison tables.

## Technical Context

**Language/Version**: Python 3.11+ (matching the SDK)
**Primary Dependencies**: existing `openmem` package only; stdlib `argparse`,
`json`, `hashlib`, `statistics`, `time`, `pathlib`, `re`. No new third-party deps.

**Facade extension required**: This feature requires one strictly-additive
change to the public `Memory` facade — exposing `wait_for_ingest(ids, user_id, timeout)`
as a thin pass-through to the underlying adapter (per [research.md §R12](research.md)).
No existing callers affected; backward-compatible per Principle III.

**Postgres embedder config**: The runner instantiates `Memory(provider="postgres", url=..., embedder="hash")`
where `embedder="hash"` is the existing local-only fallback already
implemented in `openmem.adapters.embedder`. This keeps postgres runs
zero-cost and offline-capable per [research.md §R12](research.md).
**Storage**: bundled JSONL dataset under `sdk-python/openmem/eval/datasets/`;
report file at `--report` (default `eval-report.md`); JSONL trace at
`eval-trace.jsonl`. **No on-disk cache** — every live run re-ingests so
reported numbers always reflect the provider's current behaviour.
**Testing**: pytest (existing harness). Unit tests for scorer + cost model +
dataset loader; integration tests for the CLI in dry-run mode against a stub
adapter; one optional live smoke test against postgres in Docker (matches the
existing `test_postgres_specific.py` pattern).
**Target Platform**: Linux/macOS/Windows developer workstations. Same matrix
as the existing SDK; CI runs Linux only.
**Project Type**: CLI tool packaged as a sub-module of the existing `openmem`
library (`openmem.eval`).
**Performance Goals**: full live run across 4 providers ≤ 5 minutes wall-clock
(SC-001); `--dry-run` ≤ 5 seconds (SC-004); postgres-only `--sample 10` run
≤ 30 seconds (SC-005).
**Constraints**:
- Cost ceiling USD $0.50 per full live run (SC-007).
- Reproducible: same dataset_hash + same providers ⇒ identical recall@k & MRR
  (SC-003). Latency naturally varies.
- Zero accidental cost: default behaviour MUST be dry-run; `--live` MUST
  print and confirm (above $1.00 threshold) before any paid call (FR-003,
  FR-004).
- No new third-party runtime dependencies (keep install footprint stable).
- Never run in CI (FR-011).
**Scale/Scope**: ~50 facts × ~20 queries × 4 providers = ~360 ingest calls +
~80 search calls per full live run. Bundled dataset is small enough to fit
~20 KB.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | How this plan satisfies it |
|---|---|
| I. Spec-First, Single Source of Truth (NON-NEGOTIABLE) | Eval kit consumes the existing `Memory` facade only. It introduces no new verbs, no new schema fields, no new error codes. Nothing to add to `spec/omp-0.1.openapi.yaml`. The dataset format is internal harness data, not an OMP protocol surface. |
| II. Adapter Conformance via Shared Contract Tests (NON-NEGOTIABLE) | The kit *uses* adapters via the contract surface; it does not weaken the contract. No adapter changes. The existing `test_contract_*.py` suite continues to gate every adapter unmodified. The kit's own unit tests use a stub adapter that satisfies `BaseAdapter`. |
| III. Backward and Forward Compatibility | The kit reads only fields already required on `Memory` (`id`, `content`, `user_id`, `created_at`) plus optional `tags`. It tolerates unknown fields (ignores extras in search results). No required-field changes. |
| IV. Provider Neutrality and User Sovereignty | Default `--providers postgres` works fully self-hosted with no third-party account (matches the postgres reference path). Live providers are opt-in. The kit ships zero telemetry — the only network calls are user-initiated provider API calls. Per-run `user_id` (`eval-{run_id}`) plus `--cleanup` flag protect user data sovereignty. |
| V. Open Extensibility via Namespaced Fields | The kit ignores `x-*` extension fields when scoring (it only inspects `id` and `content`). No promotion of provider-specific fields. The dataset's `tags` and `gold_fact_ids` are harness-internal, not protocol fields. |

**Result**: All five principles satisfied with no violations. No Complexity Tracking entries required.

## Project Structure

### Documentation (this feature)

```text
specs/004-eval-kit/
├── plan.md              # This file
├── spec.md              # Feature specification
├── research.md          # Phase 0 output
├── data-model.md        # Phase 1 output
├── quickstart.md        # Phase 1 output
├── contracts/
│   └── cli.md           # CLI contract (flags, exit codes, output format)
└── checklists/
    └── requirements.md  # /speckit.specify quality checklist (already passing)
```

### Source Code (repository root)

```text
sdk-python/
├── openmem/
│   ├── eval/                              # NEW sub-package
│   │   ├── __init__.py
│   │   ├── __main__.py                    # `python -m openmem.eval`
│   │   ├── cli.py                         # argparse + orchestration
│   │   ├── dataset.py                     # JSONL loader + sha256 hashing
│   │   ├── runner.py                      # ingest → wait → search pipeline
│   │   ├── scorer.py                      # recall@k, MRR, percentiles
│   │   ├── report.py                      # Markdown emitter
│   │   ├── cost.py                        # static cost-per-call table
│   │   ├── trace.py                       # JSONL trace writer
│   │   └── datasets/
│   │       └── default.jsonl              # bundled ~50-fact corpus
│   └── ...                                # existing adapters untouched
└── tests/
    └── eval/                              # NEW test directory
        ├── test_dataset.py                # loader + hash determinism
        ├── test_scorer.py                 # recall@k, MRR math
        ├── test_cost.py                   # cost-model arithmetic

        ├── test_runner_with_stub.py       # pipeline against stub adapter
        ├── test_cli_dry_run.py            # CLI flag parsing + dry-run output
        └── test_cli_live_postgres.py      # opt-in smoke (skipped without docker)
```

**Structure Decision**: Sub-package inside the existing `openmem` library
(option: single project). The harness reuses the `Memory` facade and `Capabilities`
machinery; a separate top-level package would force duplicated import paths and
break the "one `pip install openmem` ships everything" story. The CLI entry
point `openmem-eval` is registered via `pyproject.toml`'s
`[project.scripts]` table alongside the existing `omp-validate-spec` entry.

## Complexity Tracking

> No Constitution Check violations. Table intentionally empty.

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| (none) | — | — |
