# Phase 0 Research — M3.1 Eval Kit

All open questions identified during specification & planning have been
resolved. No `NEEDS CLARIFICATION` markers remain.

---

## R1. Metric definitions

**Decision**: Compute `recall@k` per query as
`|top_k_returned ∩ gold_fact_ids| / |gold_fact_ids|`, then macro-average
across queries (each query weighted equally). Compute `MRR` as the mean of
`1/rank` of the first gold fact ID in the returned list (0 if none).
Latency percentiles via `statistics.quantiles(n=100)` for p50/p95.

**Rationale**: These are the canonical IR formulations. Macro-average over
queries (not micro) so a single query with many golds doesn't dominate.
Stdlib `statistics.quantiles` avoids a numpy dependency.

**Alternatives considered**:
- nDCG: rejected — requires graded relevance, the bundled dataset is
  binary (gold or not).
- Precision@k: rejected — recall is more discriminating with small `k`
  and few golds per query (typically 1–3).
- numpy percentiles: rejected — adds a heavyweight runtime dep for a
  trivial calculation.

---

## R2. Dataset format and bundling

**Decision**: Two JSONL files in `openmem/eval/datasets/default/`:
- `facts.jsonl` — one record per line: `{"fact_id": str, "content": str, "tags": [str]}`
- `queries.jsonl` — one record per line: `{"query_id": str, "query": str, "gold_fact_ids": [str]}`

Loaded via `importlib.resources.files("openmem.eval.datasets")`. Hashed by
SHA-256 of the concatenation of both files in canonical (sorted-line) order,
truncated to 12 hex chars for display.

**Rationale**: JSONL streams cleanly, diffs cleanly in PRs, and survives
`pip install` via standard package data inclusion. Splitting facts vs.
queries lets us version the gold set independently if we add new
queries against the same corpus. `importlib.resources` is the canonical
Python 3.11+ way to read packaged data.

**Alternatives considered**:
- Single CSV / TSV: rejected — escaping commas/tabs in real text is
  error-prone.
- Parquet: rejected — adds pyarrow dep; binary diff is hostile to PR review.
- External download from a HuggingFace dataset: rejected — fails the
  "works on a fresh checkout with no external resources" requirement.

---

## R3. Cost model

**Decision**: Static dict in `openmem/eval/cost.py` keyed by
`(provider, verb)` returning `cost_usd_per_call`. Document in the file
header that values are best-effort approximations as of the file's commit
date and in the report footer that real costs may differ. Initial table:

| provider | verb | $/call | notes |
|---|---|---|---|
| postgres | * | 0.0 | local Docker, free |
| mem0 | add / search / get | 0.0001 | ~$0.10 per 1k events on paid tier |
| supermemory | add / search | 0.0 | covered by free tier of $10/mo plans |
| letta | add | 0.0001 | minimal — embed-only |
| letta | search | 0.002 | agent inference per call |

**Rationale**: A single-file static table is readable, auditable in PR
diffs, and easy to update when pricing changes. We deliberately do **not**
fetch live pricing — that would be its own infrastructure problem and
would itself need network access during dry-run.

**Alternatives considered**:
- Fetch from each provider's pricing API: rejected — no such APIs exist
  uniformly; defeats the offline-dry-run promise.
- Skip cost estimation: rejected — the spec (FR-004, SC-007) requires it
  to enforce the cost ceiling.

---

## R4. Confirmation prompt mechanics

**Decision**: When `--live` is passed and estimated cost exceeds
`--cost-threshold` (default $1.00), prompt with `Continue? [y/N]:` on stdin
unless `--yes` is also passed. In non-TTY contexts (CI, pipes), require
`--yes`; otherwise abort with exit code 3.

**Rationale**: Standard CLI convention (apt, gh, etc.). Hard refusal
without TTY prevents background scripts from accidentally racking up
charges.

**Alternatives considered**:
- Always prompt (no threshold): rejected — friction for cheap iterative
  runs.
- Always require `--yes`: rejected — bypasses the helpful interactive
  preview.

---

## R5. Ingest cache layout

**Decision**: One JSON file per `(provider, dataset_hash)` at
`~/.cache/openmem/eval/{dataset_hash}/{provider}.json` containing:
```
{"run_id": "...", "ingested_at": "...", "memory_ids": {"fact_id": "mem_..."}}
```
Cache is consulted only when `--use-cache` is passed (default off — explicit
opt-in to avoid stale-data confusion in published numbers). When stale, the
cache is overwritten silently.

**Rationale**: The cache turns mem0's ~30s ingest delay into a one-time
cost per dataset. Per-provider files allow partial cache hits (re-ingest
only the providers whose configs changed). Default-off because a maintainer
publishing numbers must trust they're fresh.

**Alternatives considered**:
- Always-on cache: rejected — risks publishing stale numbers.
- SQLite cache: rejected — over-engineered for ~50 records.
- In-memory only (no disk): rejected — defeats the purpose of caching
  across iteration cycles.

---

## R6. Trace file format

**Decision**: JSONL at `--trace` (default `eval-trace.jsonl`), one record
per API call:
```
{"ts": "2026-05-01T12:34:56Z", "provider": "mem0", "verb": "add",
 "latency_ms": 142, "status": "ok", "request_hash": "abc123", "run_id": "..."}
```
`request_hash` is sha256 of the canonicalised payload, truncated to 12
hex; full payloads are NOT logged (PII safety).

**Rationale**: JSONL streams without buffering crashes losing data;
hashed payloads protect user-supplied strings from leaking into traces
shared in bug reports.

**Alternatives considered**:
- OpenTelemetry traces: rejected — adds a heavy dep; the OTel work belongs
  in M3.3 (separate spec).
- Plain text log: rejected — un-machine-parseable for post-hoc analysis.

---

## R7. Per-run namespacing & cleanup

**Decision**: Every memory ingested by the harness uses
`user_id = f"eval-{run_id}"` where `run_id` is a fresh UUID4 per
invocation. The run_id is recorded in the cache file and the report
metadata. `--cleanup` reads the most recent run's cache and calls
`memory.delete(memory_id)` for each entry per provider; failures are
warnings, not errors.

**Rationale**: Per-run user_ids prevent eval data from polluting real
user data, satisfy the constitution's user-sovereignty principle, and
make targeted cleanup trivial (no need to scan for "evaly-looking"
memories). UUID4 avoids collisions if multiple maintainers run the kit
against the same shared backend.

**Alternatives considered**:
- Single fixed `eval` user_id: rejected — concurrent runs would interfere.
- Database-level cleanup (TRUNCATE): rejected — destroys real user data;
  not portable across providers.

---

## R8. Stub adapter for unit tests

**Decision**: Reuse the `_StubAdapter` pattern already established in
`sdk-python/tests/test_memory_facade.py` — a `BaseAdapter` subclass
that records calls and returns deterministic synthetic results. Each
eval test case can configure its stub's `search()` return values to
exercise scorer edge cases without any live backend.

**Rationale**: Pattern is already in the codebase, well-understood, and
keeps unit tests fast (sub-second) and key-free.

**Alternatives considered**:
- `unittest.mock.MagicMock`: rejected — loses type safety on the
  adapter contract; we already have a typed stub.
- An in-process fake store with real ranking: rejected — too much logic
  in test infra; the goal is to test the scorer, not the fake store.

---

## R9. Live postgres smoke test

**Decision**: One `test_cli_live_postgres.py` that runs the CLI end-to-end
against a Docker postgres container. Skips automatically when
`OMP_POSTGRES_URL` is unset (matches `test_postgres_specific.py` convention).
CI will set this env var in the existing postgres job, so the smoke runs
on every push.

**Rationale**: One real run validates that the CLI plumbing actually works,
not just the unit pieces. Postgres-only stays free, deterministic, and
fast (~30s per SC-005).

**Alternatives considered**:
- Skip live tests entirely, rely on units: rejected — units don't catch
  CLI argparse mistakes or report-write IO bugs.
- Run live tests against mem0 in CI: rejected — needs paid keys, burns
  quota, and is intentionally out of scope per FR-011.

---

## R10. Report Markdown layout

**Decision**: Single `# OMP Eval Report` document with sections:
1. **Metadata** — dataset_hash, run_id, timestamp, OMP version, providers,
   sample size, dry_run/live flag.
2. **Comparison Table** — one row per provider with columns: provider,
   recall@1, recall@5, recall@10, MRR, ingest p50/p95 (ms), search p50/p95
   (ms), errors, total wall (s), notes.
3. **Per-Provider Notes** — flagged warnings (e.g., recall@k < 0.1 →
   `[suspicious — verify configuration]`).
4. **Failure Traces** — a sub-section per provider listing the
   `query_id`, error message, and trace timestamp for each failed query.
5. **Footer** — cost-model disclaimer + dataset license.

**Rationale**: Maps cleanly to the spec acceptance scenarios. Markdown
renders correctly in GitHub release notes (the primary publication
target) and in any Markdown previewer.

**Alternatives considered**:
- HTML report with charts: rejected — not GitHub-native, requires a JS
  build step.
- JSON only: rejected — humans can't skim it; Markdown table is the
  whole point of "publish-ready".

---

## R11. `fact_id` round-trip across heterogeneous providers

**Decision**: Stamp the `fact_id` on every ingested memory in **two**
redundant places so the runner can recover it from any adapter's
`SearchResult` regardless of how that provider exposes metadata:

1. **Content prefix** (primary, always works): the runner ingests
   `content = f"[fact_id={fact_id}] {original_content}"`. The runner
   recovers the id with the regex `^\[fact_id=([^\]]+)\] ` against
   `SearchResult.memory.content`. This works for every adapter because
   `content` is a required field per OMP and is always echoed back.
2. **Tag** (secondary, supports debugging / cleanup): also pass
   `tags=[*original_tags, f"fact:{fact_id}"]`. Adapters that round-trip
   tags (postgres, supermemory) preserve this; adapters that don't
   (mem0 v2 sometimes drops tags) silently degrade and the prefix path
   takes over.

The runner extracts via:
```python
m = re.match(r"^\[fact_id=([^\]]+)\] ", result.memory.content)
fact_id = m.group(1) if m else None  # None means "not one of ours"
```
Results without a recoverable `fact_id` are filtered from the top-k
list before scoring (treated as if the provider returned fewer
results); a counter on `ProviderResult.metrics.note` flags this when
non-zero.

**Rationale**: Content is the only field every adapter is contract-bound
to round-trip verbatim (Constitution Principle III). Stamping in
content guarantees portability; the tag is a soft secondary signal that
also makes the `eval-{run_id}` data inspectable in the provider's UI.

**Alternatives considered**:
- Trust adapter-specific metadata fields (`x-fact-id` extension):
  rejected — supermemory and letta don't surface arbitrary `x-*` on
  search results uniformly; would require per-adapter parsing.
- Stamp only in tags: rejected — mem0 v2 has been observed to drop
  tag arrays in some response paths; would silently zero out recall.
- Use a separate `metadata` map: rejected — not standard on `SearchResult`.

---

## R12. `wait_for_ingest` and embedder access from the runner

**Decision**: Promote `wait_for_ingest` from adapter-private to a thin
pass-through method on the public `Memory` facade:
```python
class Memory:
    def wait_for_ingest(
        self,
        ids: list[str],
        user_id: str,
        timeout: float | None = None,
    ) -> None:
        return self._adapter.wait_for_ingest(ids, user_id=user_id, timeout=timeout)
```
The runner then calls `memory.wait_for_ingest(...)` exclusively; no
adapter-private access. For embedder configuration on the postgres
provider, the runner constructs the facade explicitly with the existing
local-only embedder (`embedder="hash"` — already the postgres adapter's
default fallback when no explicit embedder is configured), so postgres
remains zero-cost and zero-network.

**Rationale**: M2.1 already established `wait_for_ingest` as the
canonical sync barrier for async-ingest providers (mem0, supermemory).
Exposing it on the facade is consistent with `add` / `search` / `get` /
etc. and avoids the eval kit poking at `_adapter`. It's a strict
addition — no existing caller is affected — so backward compatibility
(Principle III) is preserved.

**Alternatives considered**:
- Access `memory._adapter.wait_for_ingest` directly: rejected — couples
  the eval kit to a private attribute and sets a bad precedent.
- Add a sleep-based fallback: rejected — non-deterministic; defeats
  SC-003 (reproducibility).
- Require users to wire the embedder explicitly: rejected — friction;
  the postgres adapter already has a sensible local fallback.
