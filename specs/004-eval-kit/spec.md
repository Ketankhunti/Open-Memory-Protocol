# Feature Specification: M3.1 Eval Kit (`openmem-eval`)

**Feature Branch**: `004-eval-kit`
**Created**: 2026-05-01
**Status**: Draft
**Input**: User description: "M 3.1"
**Context**: Builds on M2.1 (live-API bridges for mem0 / supermemory / letta) by adding a
benchmark harness that produces an apples-to-apples comparison report across all
configured OMP adapters.

## User Scenarios & Testing *(mandatory)*

### User Story 1 — Maintainer publishes a release-notes comparison table (Priority: P1)

A maintainer is preparing a release announcement for OMP. They want a single
command that ingests a curated dataset into every supported provider, runs the
same retrieval queries against each, and prints a Markdown table comparing
recall, precision, and latency. The numbers must reflect real provider
behaviour — not mocks — because they will be published.

**Why this priority**: Cross-provider numbers are the single most-requested
artifact for an interop SDK. Without them, OMP's "works with mem0 *and*
supermemory *and* letta *and* postgres" claim is unverified marketing copy.
This is the MVP.

**Independent Test**: Run `openmem-eval --providers postgres,mem0 --live` with
both backends configured. The command exits 0 and writes
`eval-report.md` containing per-provider rows for recall@5, MRR, ingest
p50/p95, and search p50/p95. Re-running with the same dataset hash and same
providers reproduces the recall/MRR numbers within a documented tolerance
(latency naturally varies).

**Acceptance Scenarios**:

1. **Given** `MEM0_API_KEY` and `OMP_POSTGRES_URL` are set, **When** the user
   runs `openmem-eval --providers postgres,mem0 --live --report out.md`,
   **Then** `out.md` is written with one row per provider showing recall@k,
   MRR, p50/p95 latency, and total ingest time.
2. **Given** any provider fails an individual query (timeout, quota, network),
   **When** the run completes, **Then** the report still includes that
   provider's partial numbers and an explicit `errors` column counting the
   failures, and the process exits 0.

---

### User Story 2 — Maintainer estimates cost before spending quota (Priority: P1)

Before burning paid API quota, the maintainer wants to know how many requests
will be issued and a coarse cost estimate. They run the same command with
`--dry-run` and get a per-provider breakdown of operations + estimated cost,
with no live calls executed.

**Why this priority**: Live runs against mem0/supermemory/letta cost real
money and burn rate-limited quota. The previous live-test work (T052) hit
supermemory rate limits; the harness must make accidental cost impossible.

**Independent Test**: Run `openmem-eval --providers mem0,supermemory,letta --dry-run`
without any live keys configured. The command prints a table of expected
request counts per verb per provider plus a USD cost estimate, and exits 0
without making any HTTP calls.

**Acceptance Scenarios**:

1. **Given** the user passes `--dry-run`, **When** the harness loads the
   dataset and resolves provider configs, **Then** no HTTP requests are
   made to any live provider and the printed estimate matches the dataset
   size × verb count × per-provider cost-per-call constant.
2. **Given** `--live` is passed without `--yes`, **When** the estimated cost
   exceeds a configurable threshold (default $1.00), **Then** the harness
   prints the estimate and prompts for confirmation before proceeding.

---

### User Story 3 — Maintainer iterates on a single cheap provider (Priority: P2)

While iterating on the harness itself or debugging scorer logic, the
maintainer wants to run only against `postgres` (free, local) using a
shrunken dataset for fast feedback. They pass `--providers postgres --sample 10`
and get a full report in seconds.

**Why this priority**: Without this, every iteration on the kit code itself
would burn live quota or wait minutes. This is a developer-experience
multiplier but not the headline feature.

**Independent Test**: Run `openmem-eval --providers postgres --sample 10`
against a local Docker postgres. The command completes in under 30 seconds
and the resulting report shows postgres-only metrics computed against 10
queries.

**Acceptance Scenarios**:

1. **Given** `--sample N` is passed, **When** the dataset loader runs, **Then**
   exactly `N` queries are selected (deterministically by stable hash so
   re-runs hit the same subset) and the report annotates the row as
   `sample=N`.
2. **Given** `--providers` is omitted, **When** the harness runs, **Then** it
   defaults to `postgres` only (the one provider with no key requirement).

---

### Edge Cases

- A provider's `wait_for_ingest` exceeds its budget for some facts → those
  facts are excluded from the recall computation and counted in an
  `ingest_failures` column; the run still completes.
- Network partition mid-run → per-query try/except records the error per
  provider and continues; final report lists `errors` and computes metrics
  over successful queries only with a footnote explaining sample size
  reduction.
- Provider returns zero results for every query (mis-configured embeddings,
  empty index after ingest race) → recall@k = 0 is reported as a normal
  number, not an error; the report flags providers with recall@k < 0.1 as
  `[suspicious — verify configuration]`.
- Same provider configured twice with different settings (e.g., two
  postgres URLs comparing pgvector with/without HNSW) → harness allows
  alias-suffixed provider names like `postgres:hnsw` and `postgres:flat`.
- Dataset file is missing or malformed → fail fast with a clear error
  before any provider is contacted; do not partially process.
- User passes `--live` but no provider keys are set → harness prints a
  per-provider `SKIP: missing $ENV_VAR` line and continues with whichever
  providers *are* configured; if zero are configured, exits 1.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST ship a curated dataset of approximately 50 facts
  and 20 queries with gold-truth fact IDs, bundled inside the package as a
  JSONL fixture so it works on a fresh checkout with no external download.
- **FR-002**: System MUST provide a CLI entry point `openmem-eval` (also
  invokable as `python -m openmem.eval`) that accepts at minimum:
  `--providers`, `--live`, `--dry-run`, `--report`, `--sample`, and `--yes`.
- **FR-003**: System MUST run live API calls only when `--live` is passed;
  the default behaviour MUST be `--dry-run` (no network calls) so
  accidental invocation has zero cost.
- **FR-004**: System MUST refuse to make paid API calls without first
  printing the estimated request count and cost, and MUST require explicit
  `--yes` confirmation when the estimate exceeds a configurable threshold
  (default USD $1.00).
- **FR-005**: System MUST execute a fixed pipeline per provider per run:
  ingest the dataset → wait for ingest completion (using
  `adapter.wait_for_ingest`) → run the query set → score results → record
  per-call latency.
- **FR-006**: System MUST compute and report at minimum the following
  metrics per provider: recall@k (k=1, 5, 10), MRR, ingest p50 / p95
  latency, search p50 / p95 latency, total wall-clock time, error count.
- **FR-007**: System MUST emit a Markdown report at the path given by
  `--report` (default `eval-report.md`) containing a comparison table, a
  metadata block (dataset hash, OMP version, run timestamp, per-provider
  config hash), and a per-query trace section for failed queries only.
- **FR-008**: *(removed — caching dropped; every live run re-ingests so
  reported numbers always reflect the provider's current behaviour.)*
- **FR-009**: System MUST handle per-query and per-provider failures
  gracefully: a failed query for one provider MUST NOT abort the run for
  other providers, and per-provider failures MUST be recorded in the
  report rather than raised.
- **FR-010**: System MUST detect missing per-provider environment
  variables (`MEM0_API_KEY`, `SUPERMEMORY_API_KEY`, `LETTA_API_KEY`,
  `OMP_POSTGRES_URL`) and emit a `SKIP: missing $ENV` line for each
  unconfigured provider rather than raising.
- **FR-011**: System MUST NOT be wired into CI pull-request or main-branch
  workflows; it is a manually-invoked tool only. (A documentation note in
  the README MUST state this explicitly.)
- **FR-012**: System MUST log every live API call to a JSONL trace file
  (default `eval-trace.jsonl`) with timestamp, provider, verb, latency,
  status, and a redacted request hash, to support post-hoc debugging.
- **FR-013**: Users MUST be able to pass `--sample N` to deterministically
  reduce the query set to the first N entries by stable hash so cheap
  iteration is possible.
- **FR-014**: System MUST namespace every memory it ingests under a
  unique per-run `user_id` (e.g., `eval-{run_id}`) so eval data never
  collides with real user data and can be cleaned up after the run.
- **FR-015**: System MUST provide a `--cleanup` flag that, when passed,
  deletes every memory created by the most recent run from each provider
  before exiting.

### Key Entities

- **Dataset**: A versioned JSONL fixture of `{fact_id, content, tags}`
  records plus a `{query_id, query, gold_fact_ids}` set. Identified by
  SHA-256 of its serialised contents (the `dataset_hash`).
- **RunConfig**: The user's invocation parameters: providers, sample size,
  live/dry-run, output paths, run_id (auto-generated UUID).
- **ProviderResult**: Per-provider output of a run, containing ingest
  latencies, per-query results (top-k fact IDs + latencies), error list,
  and computed metrics.
- **Report**: The Markdown artifact emitted at `--report`, containing the
  comparison table, run metadata, and failure traces.
- **TraceEntry**: One record in the JSONL trace, capturing every API call
  made during a live run.
- **CostModel**: A static table of estimated cost-per-call per provider
  per verb, used by `--dry-run` to print the cost estimate. Values are
  best-effort approximations and explicitly documented as such in the
  report.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A maintainer can produce a four-provider comparison report
  (`postgres + mem0 + supermemory + letta`) with one command and total
  wall-clock under 5 minutes, given all keys are configured.
- **SC-002**: The default invocation (`openmem-eval` with no flags) makes
  zero network calls and prints a cost estimate; running it without keys
  set never produces a paid charge.
- **SC-003**: Re-running the same command against the same dataset with
  the same providers reproduces recall@k and MRR exactly (latency
  metrics naturally vary).
- **SC-004**: A `--dry-run` invocation against all four providers
  completes in under 5 seconds and prints a per-provider request count
  and USD cost estimate.
- **SC-005**: A `--providers postgres --sample 10` run against local
  Docker postgres completes in under 30 seconds end-to-end including
  ingest, search, and report write.
- **SC-006**: When any single provider fails 100% of its queries, the
  other providers' metrics are still produced and the report clearly
  flags the failed provider; the process exit code is 0 (success).
- **SC-007**: The bundled dataset is small enough that one full live run
  across all four providers stays under the documented cost ceiling
  (USD $0.50 per run with current published pricing).

## Assumptions

- The maintainer running live evaluations has API keys for the providers
  they want to compare and is responsible for the cost; the harness only
  enforces visibility and confirmation, not spending limits.
- The four adapters in scope are the ones already shipping in M2.1:
  `postgres`, `mem0`, `supermemory`, `letta`. The kit must be
  extensible to additional adapters but that is out of scope here.
- The bundled dataset will be hand-curated for diversity (different
  topics, different query phrasings) but is small (~50 facts) and is
  not intended to substitute for domain-specific evaluation by users.
- "Recall@k" is computed as `|top_k ∩ gold_fact_ids| / |gold_fact_ids|`
  per query, then averaged across queries. MRR is computed in the
  standard way over the rank of the first gold hit.
- A local-only embedder (already shipped via `openmem.adapters.embedder`)
  is used for postgres so it remains free; live providers use whichever
  embedder they manage internally.
- The cost model is a static table maintained by hand based on
  publicly-listed pricing at the time of writing; it is documented in
  the report as approximate and not a guarantee.
- The kit is not a CI gate. Benchmarks intentionally do not run on
  pull requests or pushes to main, because they need keys and time.
- Dataset versioning lives inside the package (`openmem/eval/datasets/`)
  so a `pip install --upgrade openmem` ships any dataset updates.
- The harness is sequential per provider but may run providers in
  parallel via `asyncio.gather` once correctness is established;
  parallelism is a P3 concern and not required for the MVP.
