---
description: "Tasks for M3.1 Eval Kit (`openmem-eval`)"
---

# Tasks: M3.1 Eval Kit (`openmem-eval`)

**Input**: Design documents from `/specs/004-eval-kit/`
**Prerequisites**: [plan.md](plan.md), [spec.md](spec.md), [research.md](research.md), [data-model.md](data-model.md), [contracts/cli.md](contracts/cli.md), [quickstart.md](quickstart.md)
**Tests**: Included — the spec calls for unit tests on every component (scorer, cost, dataset) plus a CLI dry-run integration test and an opt-in postgres live smoke test.

**Format**: `- [ ] TID [P?] [Story?] Description`

---

## Phase 1: Setup

- [X] T001 Create the eval sub-package skeleton: `sdk-python/openmem/eval/__init__.py`, `sdk-python/openmem/eval/__main__.py` (re-exports `main` from `cli.py`), and the empty test directory `sdk-python/tests/eval/__init__.py`.
- [X] T002 Register the CLI entry point: in `sdk-python/pyproject.toml` add `openmem-eval = "openmem.eval.cli:main"` to the `[project.scripts]` table next to the existing `omp-validate-spec` entry, and include `openmem/eval/datasets/*.jsonl` in `[tool.setuptools.package-data]` so the bundled dataset ships with `pip install`.
- [X] T003 [P] Update [sdk-python/README.md](sdk-python/README.md) with a one-line note under "Tooling" stating that `openmem-eval` is a manual benchmark tool, never run in CI, and link to [specs/004-eval-kit/quickstart.md](specs/004-eval-kit/quickstart.md).

---

## Phase 2: Foundational (Blocking Prerequisites)

These types and IO primitives are imported by every later module.

- [X] T004 [P] Implement dataclasses for the data model in `sdk-python/openmem/eval/types.py`: `Fact`, `Query`, `Dataset`, `RunConfig`, `QueryResult`, `ErrorRecord`, `Metrics`, `ProviderResult`. All immutable (`frozen=True`) where practical. Match the field names and types defined in [data-model.md](specs/004-eval-kit/data-model.md).
- [X] T005 [P] Bundle the default dataset: write `sdk-python/openmem/eval/datasets/default/facts.jsonl` (~50 facts, hand-curated for topical diversity — recipes, schedules, places, identifiers, preferences) and `sdk-python/openmem/eval/datasets/default/queries.jsonl` (~20 queries with `gold_fact_ids` referencing only existing fact ids).
- [X] T006 Implement `sdk-python/openmem/eval/dataset.py`: `load_default() -> Dataset`, `load_path(path) -> Dataset`, `dataset_hash(dataset) -> str` (SHA-256 of canonical sorted-line concatenation, first 12 hex chars). Use `importlib.resources.files("openmem.eval.datasets.default")` to read the bundled files. Validate uniqueness of `fact_id`, non-empty `content`, and that every entry in `gold_fact_ids` resolves to a known fact.

**Checkpoint**: Dataset loads, types compile. User story work can begin.

---

## Phase 3: User Story 1 — Maintainer publishes a release-notes comparison table (Priority: P1) 🎯 MVP

**Goal**: One command — `openmem-eval --providers postgres,mem0 --live` — produces a Markdown comparison report with recall@k, MRR, and p50/p95 latency per provider.

**Independent Test**: With `OMP_POSTGRES_URL` and `MEM0_API_KEY` set, the command exits 0 and writes `eval-report.md` containing rows for both providers. Re-running the same command reproduces recall@k and MRR exactly (latency varies).

### Tests for User Story 1

- [X] T007 [P] [US1] Tests for the dataset loader and hashing in `sdk-python/tests/eval/test_dataset.py` covering: bundled load returns ≥50 facts and ≥20 queries; `dataset_hash` is deterministic across two loads; mutating one fact changes the hash; gold-fact-id pointing to a missing fact raises a clear error.
- [X] T008 [P] [US1] Tests for the scorer in `sdk-python/tests/eval/test_scorer.py` covering: recall@1 / @5 / @10 with multiple golds; MRR with gold at rank 1, 3, none; macro-average across queries; empty result list returns recall=0 and MRR=0; latency p50/p95 against a known list.
- [X] T009 [P] [US1] Tests for the report writer in `sdk-python/tests/eval/test_report.py` covering: Markdown table renders one row per provider with the seven required columns; metadata block includes dataset_hash, run_id, OMP version, timestamp; suspicious-flag block appears when recall@5 < 0.1; failure traces section appears only when `errors` is non-empty.
- [X] T010 [US1] Integration test for the runner against a stub adapter in `sdk-python/tests/eval/test_runner_with_stub.py` covering: (a) ingest pipeline calls `add` for every fact with the `[fact_id=...]` prefix and `fact:<id>` tag stamped per [research.md §R11](specs/004-eval-kit/research.md); (b) `Memory.wait_for_ingest` is invoked with all returned ids; (c) search runs once per query and the runner recovers `fact_id` via the prefix regex; (d) per-call latencies are recorded; (e) partial failures (one query raises) populate `ErrorRecord` and the run still completes; (f) **reproducibility (SC-003)** — running the pipeline twice with the same stub returns equal `Metrics` dicts; (g) **multi-provider continuation (SC-006)** — with two stub providers where providerA raises on every search and providerB succeeds, the run exits 0, providerA's `ProviderResult.status == "failed"`, providerB's metrics are populated.
- [X] T011 [US1] Live smoke test in `sdk-python/tests/eval/test_cli_live_postgres.py` that invokes the CLI end-to-end against the local Docker postgres (skip when `OMP_POSTGRES_URL` is unset, matching `test_postgres_specific.py` convention). Asserts exit 0, `eval-report.md` exists, contains a `postgres` row with non-zero ingest latency, and the run completes in ≤ 30 seconds wall-clock (validates SC-005).

### Implementation for User Story 1

- [X] T012 [P] [US1] Implement `sdk-python/openmem/eval/scorer.py`: `recall_at_k(top_k_fact_ids, gold_fact_ids, k) -> float`, `mrr(top_k_fact_ids, gold_fact_ids) -> float`, `compute_metrics(provider_result, ks=(1, 5, 10)) -> Metrics`. Percentiles via `statistics.quantiles(n=100)`; safe handling of empty inputs (return 0.0).
- [X] T013 [P] [US1] Implement `sdk-python/openmem/eval/cost.py`: the `COST_USD_PER_CALL` static dict covering verbs `add`, `search`, `get`, **and `delete`** (postgres free; mem0/letta per-call estimates from [research.md](specs/004-eval-kit/research.md) §R3; `delete` rows: postgres 0, mem0 0.0001, supermemory 0, letta 0.0001) and `estimate(provider, verb, n_calls) -> float`. Unknown `(provider, verb)` returns 0.0 and emits a stderr warning once per pair.
- [X] T014 [P] [US1] Implement `sdk-python/openmem/eval/trace.py`: `TraceWriter(path)` with `write(provider, verb, latency_ms, status, payload)` that appends a JSONL line. Hash the payload via SHA-256 (first 12 hex) — never log raw payload content. Open the file lazily on first write so dry-runs leave no trace file.
- [X] T015 [US1] Implement `sdk-python/openmem/eval/runner.py`: `run_provider(provider_name, dataset, run_config, trace_writer) -> ProviderResult`. The pipeline is: resolve `Memory(provider=...)` (postgres uses `embedder="hash"` per [research.md §R12](specs/004-eval-kit/research.md) for zero-cost local embedding) → for each fact, stamp `content = f"[fact_id={fact.fact_id}] {fact.content}"` and `tags = [*fact.tags, f"fact:{fact.fact_id}"]` per [research.md §R11](specs/004-eval-kit/research.md), call `add()` (record latency, capture per-fact errors) → `memory.wait_for_ingest(memory_ids, user_id=user_id)` (using the new facade pass-through from T015a; capture timeout as `ingest_failures`) → for each query `search(query, user_id, limit=10)` (parse `fact_id` from `result.memory.content` via the regex `^\[fact_id=([^\]]+)\] `; results without a match are filtered out before scoring, plus latency, plus error). Use `user_id = f"eval-{run_config.run_id}"` for every call.
- [X] T015a [US1] Add `Memory.wait_for_ingest(ids, user_id, timeout=None)` thin pass-through to `sdk-python/openmem/memory.py` per [research.md §R12](specs/004-eval-kit/research.md): forwards directly to `self._adapter.wait_for_ingest(ids, user_id=user_id, timeout=timeout)`. Strictly additive; no existing callers affected. Add a unit test in `sdk-python/tests/test_memory_facade.py` confirming it forwards through the stub adapter.
- [X] T016 [US1] Implement `sdk-python/openmem/eval/report.py`: `write_report(path, run_config, dataset, results)` emitting the Markdown sections defined in [research.md](specs/004-eval-kit/research.md) §R10 — Metadata table, Comparison table, Per-Provider Notes (suspicious flags), Failure Traces, Footer. Format latencies as `p50/p95 ms`; format recall to two decimal places; format MRR to two decimal places.
- [X] T017 [US1] Implement `sdk-python/openmem/eval/cli.py` minimal flag set for US1: `--providers`, `--live`, `--report`, `--trace`, `--verbose`. `main(argv=None) -> int` returns the documented exit codes from [contracts/cli.md](specs/004-eval-kit/contracts/cli.md). On `--live` without keys, print `[skip] <provider>: missing $ENV_VAR` to stderr per provider and exit 1 only when zero providers remain.
- [X] T018 [US1] Wire `sdk-python/openmem/eval/__main__.py` to call `cli.main()` so `python -m openmem.eval` works equivalently to the `openmem-eval` script entry.
- [X] T018a [US1] Implement `--cleanup` (FR-015) in `sdk-python/openmem/eval/cli.py` and `sdk-python/openmem/eval/cleanup.py`: list every memory under `user_id = f"eval-{run_id}"` and call `Memory(provider=...).delete(memory_id)` for each, logging failures as warnings. Add a unit test in `sdk-python/tests/eval/test_cleanup.py` against a stub adapter that verifies all listed `memory_id`s are deleted and that pagination is honoured. Note: placed in Phase 3 (not Polish) because FR-015 is a hard requirement and `eval-{run_id}` namespacing in T015 exists specifically to enable it.

**Checkpoint**: US1 MVP done — a maintainer with `OMP_POSTGRES_URL` and `MEM0_API_KEY` can publish a real comparison table.

---

## Phase 4: User Story 2 — Maintainer estimates cost before spending quota (Priority: P1)

**Goal**: `openmem-eval --dry-run` (the default) prints a per-provider request-count + USD-cost table without making any HTTP calls. `--live` above the threshold prompts for confirmation.

**Independent Test**: With no API keys set, `openmem-eval --providers mem0,supermemory,letta --dry-run` exits 0 and prints a cost-estimate table; verify zero network calls were made (mock httpx and assert no requests).

### Tests for User Story 2

- [X] T019 [P] [US2] Tests for the cost model in `sdk-python/tests/eval/test_cost.py` covering: known `(provider, verb)` returns the static value × n_calls; unknown pair returns 0.0 and emits exactly one stderr warning per pair; total estimate sums correctly across providers; **cost-ceiling guard (SC-007)** — `estimate` for a full four-provider run (`postgres + mem0 + supermemory + letta`) against the bundled default dataset returns ≤ USD 0.50.
- [X] T020 [P] [US2] Tests for CLI dry-run mode in `sdk-python/tests/eval/test_cli_dry_run.py` covering: `--dry-run` makes zero adapter instantiations (patch `openmem.Memory.__init__` to raise); estimate table includes one row per provider with adds/searches/waits counts derived from dataset size; `--providers` defaulting to `postgres` produces a $0.00 estimate; missing keys are not enforced in dry-run mode (no skip lines printed).
- [X] T021 [P] [US2] Tests for the confirmation prompt in `sdk-python/tests/eval/test_cli_confirm.py` covering: estimated cost ≤ threshold proceeds without prompt; cost > threshold with TTY prompts and accepts `y` (proceeds) / `n` (exits 3); cost > threshold without TTY and without `--yes` exits 3; cost > threshold with `--yes` proceeds without prompting.

### Implementation for User Story 2

- [X] T022 [US2] Extend `sdk-python/openmem/eval/cli.py` with `--dry-run` (default true), `--cost-threshold` (default 1.00), and `--yes`. Add a `dry_run_estimate(run_config, dataset) -> dict[str, dict[str, int]]` helper that returns per-provider verb counts purely from dataset size (no adapter calls). Print the estimate table to stdout in the format defined in [contracts/cli.md](specs/004-eval-kit/contracts/cli.md).
- [X] T023 [US2] Implement `sdk-python/openmem/eval/confirm.py`: `confirm_or_exit(estimated_cost_usd, threshold, yes, isatty) -> None`. Returns silently when cost ≤ threshold or `yes` is true; prompts on TTY otherwise; raises `SystemExit(3)` on refusal or non-TTY without `--yes`. Wire into `cli.main` immediately before any adapter is instantiated.
- [X] T024 [US2] In `sdk-python/openmem/eval/cli.py`, store `--live` as a regular argparse flag (default False) and derive `dry_run = not args.live` rather than using a mutually-exclusive group. Reject the explicit combination `--dry-run --live` at parse time with a clear error (`--dry-run and --live are mutually exclusive`). Document this wiring decision inline as a comment referencing [contracts/cli.md](specs/004-eval-kit/contracts/cli.md) §"Mutual exclusion".

**Checkpoint**: US2 done — accidental quota burn is impossible. Default invocation makes zero calls.

---

## Phase 5: User Story 3 — Maintainer iterates on a single cheap provider (Priority: P2)

**Goal**: `--providers postgres --sample 10` runs the full pipeline end-to-end against local postgres with 10 queries in under 30 seconds.

**Independent Test**: With `OMP_POSTGRES_URL` set and Docker postgres up, run `openmem-eval --providers postgres --sample 10 --live`; assert the run completes in ≤ 30s and the report annotates `sample=10`.

### Tests for User Story 3

- [X] T025 [P] [US3] Tests for `--sample` in `sdk-python/tests/eval/test_dataset.py` (extends T007's file): `sample(dataset, n)` returns exactly `n` queries; selection is deterministic (same `n` ⇒ same query_ids across runs); `n` larger than total returns all queries with a warning; `n=0` raises ValueError.

### Implementation for User Story 3

- [X] T027 [US3] Add `sample(dataset, n) -> Dataset` to `sdk-python/openmem/eval/dataset.py`. Selection is the first `n` queries after sorting by `query_id`'s SHA-256 hash (stable, deterministic). Return a new `Dataset` with all original facts but only the sampled queries.
- [X] T031 [US3] Update `sdk-python/openmem/eval/report.py` to annotate the comparison-table caption with `sample=N` whenever `run_config.sample` is set.

*(T026, T028, T029, T030 removed — caching dropped per project decision; every live run re-ingests so reported numbers always reflect current provider behaviour.)*

**Checkpoint**: US3 done — cheap iteration loop is sub-30s on postgres-only.

---

## Phase 6: Polish & Cross-Cutting Concerns

- [X] T032 [P] Add `--version` to `sdk-python/openmem/eval/cli.py` printing `openmem X.Y.Z, dataset default-<hash>` and exiting 0. Add a one-line test in `tests/eval/test_cli_dry_run.py`.
- [X] T033 [P] Suspicious-recall flag wiring: in `sdk-python/openmem/eval/scorer.py` set `Metrics.note = "suspicious — recall@5 < 0.1"` when applicable; assert the report renders it (extends T009).
- [X] T034 [P] Run [specs/004-eval-kit/quickstart.md](specs/004-eval-kit/quickstart.md) end-to-end on a local checkout: install, `openmem-eval` (default), `--dry-run` for all four providers, `--live --providers postgres --sample 10`. Note any drift in a follow-up task; do NOT silently fix the quickstart.
- [X] T035 Confirm coverage stays green: `python -m pytest sdk-python/tests --cov=openmem --cov-report=term --timeout=60` shows ≥ 85% total, and the new `openmem.eval` modules each report ≥ 85% individually. Add narrowly-targeted tests for any module under 85% before committing.
- [X] T036 README pass: ensure [sdk-python/README.md](sdk-python/README.md) Tooling section + [README.md](README.md) at repo root mention `openmem-eval` exactly once, link to the quickstart, and explicitly state it never runs in CI (FR-011).

---

## Dependencies & Execution Order

### Phase Dependencies

- **Phase 1 (Setup)**: T001 → T002. T003 in parallel with T002.
- **Phase 2 (Foundational)**: T004, T005 in parallel; T006 depends on both. **Blocks all later phases.**
- **Phase 3 (US1)**: T007–T011 (tests) in parallel before implementation. T012, T013, T014 in parallel. T015a depends on the existing facade (no eval deps); T015 depends on T012+T013+T014+T015a. T016 depends on T012. T017 depends on T015+T016. T018 depends on T017. T018a depends on T018 only (lists memories by `user_id` — no cache).
- **Phase 4 (US2)**: T019, T020, T021 in parallel before implementation. T022 → T023 → T024.
- **Phase 5 (US3)**: T025 before T027. T031 standalone.
- **Phase 6 (Polish)**: T032, T033 in parallel. T034, T035, T036 sequential at the end.

### User Story Dependencies

- **US1 (P1)**: needs Phases 1+2 only.
- **US2 (P1)**: needs Phases 1+2 + the `cli.py` skeleton from T017 (so US1 must reach T017 first).
- **US3 (P2)**: needs Phases 1+2 + the runner from T015; cleanest if US1 is fully done first.

### Within Each User Story

- Tests (T007–T011, T019–T021, T025) are written first and MUST fail before implementation.
- Models / utilities (scorer, cost, trace, dataset.sample) before runner integration.
- Runner before CLI wiring.
- CLI wiring before live smoke test.

### Parallel Opportunities

- T004 (types) ∥ T005 (dataset files).
- T007 ∥ T008 ∥ T009 (independent test files).
- T012 (scorer) ∥ T013 (cost) ∥ T014 (trace).
- T019 ∥ T020 ∥ T021.
- T025 ∥ T026.
- T032 ∥ T033 ∥ T034.

---

## Parallel Example: User Story 1 implementation kickoff

Once tests T007–T011 are red:

```powershell
# Three independent files, no shared state — run agents in parallel:
# Agent A: implement openmem/eval/scorer.py to make test_scorer.py green
# Agent B: implement openmem/eval/cost.py to make test_cost.py (in US2) compile
# Agent C: implement openmem/eval/trace.py (no failing test yet, but unblocks runner)
```

Then sequentially: T015 (runner) → T016 (report) → T017 (cli) → T018 (entrypoint).

---

## Implementation Strategy

**MVP scope** = Phases 1 + 2 + 3 (User Story 1 only). At MVP a maintainer can
already publish a comparison table for `postgres + mem0` — every other story
adds safety (US2: cost guard) or developer-experience polish (US3: cheap
iteration). Recommended order:

1. Land MVP (T001–T018), ship the first real comparison report.
2. Layer in US2 (T019–T024) before any wider-audience use, so the cost guard
   protects new contributors who pull and run it.
3. Layer in US3 (T025–T031) when iterating on the dataset or scorer.
4. Polish (T032–T037) before the M3.1 release tag.

**Format validation**: every task above is `- [ ] TID [P?] [US?] description`
with an explicit file path or paths. Setup, Foundational, and Polish tasks
intentionally carry no `[USx]` label per the format rules.
