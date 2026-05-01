# Phase 1 Data Model — M3.1 Eval Kit

All entities below are internal to the harness (`openmem.eval`). None are
OMP protocol surfaces; they do not appear in `spec/omp-0.1.openapi.yaml`.

---

## Dataset

Bundled JSONL corpus the harness ingests and queries against.

| Field | Type | Required | Description |
|---|---|---|---|
| `facts` | list[Fact] | yes | Records to ingest into each provider. |
| `queries` | list[Query] | yes | Queries to run against each provider. |
| `dataset_hash` | str | yes (derived) | SHA-256(facts.jsonl ‖ queries.jsonl), first 12 hex chars. |

### Fact

| Field | Type | Required | Description |
|---|---|---|---|
| `fact_id` | str | yes | Stable identifier used in `gold_fact_ids`. Format `f-NNNN`. |
| `content` | str | yes | The text payload sent as `Memory.add(content=...)`. |
| `tags` | list[str] | no | Optional tags forwarded to the adapter. |

**Validation**:
- `fact_id` must be unique within the dataset.
- `content` must be non-empty.
- All `tags` items must be lowercase ASCII slugs.

**Ingest stamping** (per [research.md §R11](research.md)):
The runner does NOT pass `content` raw. It rewrites it as
`f"[fact_id={fact_id}] {content}"` and appends `f"fact:{fact_id}"` to the
tag list before calling `Memory.add(...)`. Recovery from `SearchResult`
uses the regex `^\[fact_id=([^\]]+)\] ` against the returned content.
Results whose content does not match are filtered from the top-k before
scoring.

### Query

| Field | Type | Required | Description |
|---|---|---|---|
| `query_id` | str | yes | Stable identifier. Format `q-NNNN`. |
| `query` | str | yes | The query string passed to `Memory.search(query=...)`. |
| `gold_fact_ids` | list[str] | yes | The fact IDs that should appear in the top-k for a correct retrieval. |

**Validation**:
- Every entry in `gold_fact_ids` must reference an existing `fact_id`.
- `gold_fact_ids` must be non-empty (a query with no answer is not a
  valid eval input).

---

## RunConfig

Captures the user's invocation. Built once from CLI flags; immutable for
the rest of the run.

| Field | Type | Default | Description |
|---|---|---|---|
| `providers` | list[str] | `["postgres"]` | Provider names; aliases like `postgres:hnsw` allowed. |
| `live` | bool | `false` | If false, dry-run (no network). |
| `dry_run` | bool | `true` | Inverse of `live`; redundant for clarity. |
| `report_path` | Path | `eval-report.md` | Destination Markdown file. |
| `trace_path` | Path | `eval-trace.jsonl` | Destination JSONL trace file. |
| `sample` | int \| None | `None` | If set, take first N queries by stable hash order. |
| `cost_threshold_usd` | float | `1.00` | Above this, prompt before running. |
| `yes` | bool | `false` | Skip confirmation prompt. |
| `cleanup` | bool | `false` | If true, after run, delete every memory created. |
| `run_id` | str | (UUID4) | Auto-generated; embedded in user_ids. |

**Derived**:
- `user_id` per provider = `f"eval-{run_id}"`.

**Validation**:
- `live` and `dry_run` MUST be opposites.
- If `live` is true and stdin is not a TTY and `yes` is false → exit 3.
- Unknown provider names → exit 2 with the list of known names.

---

## ProviderResult

One per provider in the run.

| Field | Type | Description |
|---|---|---|
| `provider` | str | Provider name (possibly aliased). |
| `status` | str | `ok` / `skipped` / `partial` / `failed`. |
| `skip_reason` | str \| None | E.g., `"missing $MEM0_API_KEY"`. |
| `ingest_latencies_ms` | list[float] | One entry per fact actually ingested. |
| `search_latencies_ms` | list[float] | One entry per query actually executed. |
| `query_results` | list[QueryResult] | Per-query top-k + score outcome. |
| `errors` | list[ErrorRecord] | Captured exceptions (one per failed call). |
| `metrics` | Metrics | Computed from the above. |
| `total_wall_s` | float | Wall-clock from start to finish for this provider. |

### QueryResult

| Field | Type | Description |
|---|---|---|
| `query_id` | str | From the dataset. |
| `top_k_fact_ids` | list[str] | Top-k `fact_id`s extracted from the search response. |
| `latency_ms` | float | Single search call latency. |
| `error` | str \| None | Set if this single query failed. |

### Metrics

| Field | Type | Description |
|---|---|---|
| `recall_at_1` | float | 0.0–1.0 |
| `recall_at_5` | float | 0.0–1.0 |
| `recall_at_10` | float | 0.0–1.0 |
| `mrr` | float | 0.0–1.0 |
| `ingest_p50_ms` | float | Computed when ingest_latencies_ms is non-empty. |
| `ingest_p95_ms` | float | Same. |
| `search_p50_ms` | float | Computed when search_latencies_ms is non-empty. |
| `search_p95_ms` | float | Same. |
| `error_count` | int | Length of `errors`. |
| `note` | str \| None | E.g., `"suspicious — recall@5 < 0.1"`. |

### ErrorRecord

| Field | Type | Description |
|---|---|---|
| `verb` | str | `add` / `search` / `wait_for_ingest` / etc. |
| `query_id` | str \| None | Set for per-query failures. |
| `fact_id` | str \| None | Set for per-fact ingest failures. |
| `error_class` | str | Python exception class name. |
| `message` | str | Truncated to 200 chars. |
| `ts` | str | ISO 8601 UTC. |

---

## Report

Single Markdown file. Rendered from `RunConfig` + `list[ProviderResult]` +
the dataset metadata.

Sections (in order):
1. Title + metadata table.
2. Comparison table (one row per provider).
3. Per-provider notes (suspicious-flag block).
4. Failure traces (only sections for providers with `errors`).
5. Cost-model disclaimer footer + dataset license note.

---

## TraceEntry

One JSON object per line in `eval-trace.jsonl`.

| Field | Type | Description |
|---|---|---|
| `ts` | str | ISO 8601 UTC. |
| `run_id` | str | From RunConfig. |
| `provider` | str | Provider name. |
| `verb` | str | API verb. |
| `latency_ms` | float | Wall-clock for this single call. |
| `status` | str | `ok` / `error`. |
| `request_hash` | str | SHA-256 of canonical payload, 12 hex chars. PII never logged. |
| `error_class` | str \| None | When `status=="error"`. |

---

## CostModel

Static dict (`dict[tuple[str, str], float]`) at module scope in
`openmem/eval/cost.py`. Read-only at runtime.

```python
COST_USD_PER_CALL: dict[tuple[str, str], float] = {
    ("postgres", "add"):    0.0,
    ("postgres", "search"): 0.0,
    ("mem0", "add"):        0.0001,
    ("mem0", "search"):     0.0001,
    ("mem0", "get"):        0.0001,
    ("supermemory", "add"): 0.0,
    ("supermemory", "search"): 0.0,
    ("letta", "add"):       0.0001,
    ("letta", "search"):    0.002,
}
```

Function: `estimate(provider: str, verb: str, n_calls: int) -> float`.
Unknown `(provider, verb)` defaults to `0.0` and is logged as a warning.

---

## Relationships

```
RunConfig 1 ── many ProviderResult ── 1 Metrics
                       │
                       └── many QueryResult, many ErrorRecord
Dataset 1 ── many Fact, many Query
Query  ── references ──> Fact (via gold_fact_ids)
Each API call ── appended ──> TraceEntry (in eval-trace.jsonl)
```
