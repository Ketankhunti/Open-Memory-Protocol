# Phase 1 — Data Model: M2.1 Live-API bridges

**Feature**: `003-m2-1-live` | **Date**: 2026-04-28

This file documents the data-model deltas introduced by M2.1. Anything not
listed here is unchanged from M1/M2.

---

## 1. `Memory.status` (NEW — FR-122)

Added to `spec/omp-0.1.openapi.yaml` Memory schema:

```yaml
Memory:
  type: object
  properties:
    # ... existing fields unchanged ...
    status:
      type: string
      enum: [queued, indexing, done, failed]
      description: |
        Ingestion lifecycle state. OPTIONAL. Synchronous providers
        (postgres, passthrough-to-postgres, letta) MUST report `done`.
        Asynchronous providers (mem0, supermemory) MUST report the
        upstream value. Absent / null means the adapter does not track
        ingestion state (legacy clients are unaffected).
```

Pydantic mirror in `openmem/types.py`:

```python
class Memory(BaseModel):
    # ... existing fields ...
    status: Optional[Literal["queued", "indexing", "done", "failed"]] = None
    model_config = ConfigDict(extra="allow")
```

Per-adapter emission rules:

| Adapter      | `status` emitted by `add()`                  | `status` emitted by `get()` / `list()` / `search()` |
|--------------|----------------------------------------------|------------------------------------------------------|
| postgres     | `"done"` (always)                            | `"done"` (always)                                    |
| passthrough  | mirror upstream (may be `None`)              | mirror upstream                                      |
| mem0         | `"queued"`                                   | `"done"` once polled successfully; never `failed`    |
| supermemory  | `"queued"`                                   | upstream value (`queued / chunking / indexing / done`)|
| letta        | `"done"` (synchronous)                       | `"done"`                                             |

`status` MUST round-trip through `model_dump_json` / `model_validate_json`
unchanged. The new contract test `test_status_round_trips` (added to
`test_contract_lifecycle.py`) asserts this for every adapter.

---

## 2. `OMPError.code` — new value `"ingestion_timeout"` (additive)

Added to `Error.code` enum in OpenAPI:

```yaml
Error:
  properties:
    code:
      enum:
        # ... existing values ...
        - ingestion_timeout    # NEW
```

Raised by `mem0.get(id)` and `supermemory.get(id)` when the bounded
poll budget elapses without the provider transitioning the record to
`done`. Carries `provider=<name>` and `details={"event_id": ..., "elapsed": ...}`.

`openmem.errors.ProviderError` gains no new subclass; the code field
discriminates.

---

## 2a. Pagination cursor format (binds FR-103, FR-108)

OMP `next_cursor` is OPAQUE to callers but pinned to a single internal
format across all adapters that paginate by integer page (mem0,
supermemory):

- Format: `next_cursor = base64.urlsafe_b64encode(json.dumps({"page": N+1}).encode()).decode().rstrip("=")`
- Decode: `page = json.loads(base64.urlsafe_b64decode(cursor + "==")) ["page"]`
- Empty / missing cursor on input → `page = 1` (1-indexed; matches mem0
  + supermemory upstream).
- `next_cursor = None` when the upstream response signals no more pages
  (`next == null` for mem0; `currentPage >= totalPages` for supermemory).
- The base64 wrapping is a deliberate **opacity barrier** — callers
  MUST treat `next_cursor` as a black box. The wrapping also makes
  cursor-injection attacks (e.g. caller crafting `page=999999` to
  exhaust quota) detectable by adapters that validate the decoded JSON
  shape before forwarding to upstream.
- Cursors received from untrusted callers (e.g. via passthrough HTTP)
  MUST be validated: decode failure or non-integer `page` value MUST
  raise `InvalidRequestError(message="malformed cursor")` and MUST NOT
  be forwarded upstream.

---

## 3. Async-ingestion record (transient)

Definition: A `Memory` returned by `add()` whose `status != "done"`.

Invariants:

1. `id` is the provider-assigned id (NOT a synthetic placeholder).
2. `content` is the **original** user-supplied text (NOT a placeholder
   and NOT the provider's eventual rewritten value — the rewrite, if
   any, is surfaced by a later `get(id)`).
3. `created_at` is the local wall-clock at the moment `add()` returned
   (used by callers for "how long has this been queued?" diagnostics).
4. `x-{provider}.event_id` carries the upstream event/job id when the
   provider returns one (mem0 always; supermemory implicit via `id`).
5. `x-{provider}.original_content` mirrors `content` only if the provider
   is known to LLM-rewrite (mem0). For other providers it is omitted to
   avoid wasted bytes.

Lifecycle:

```text
add()              → Memory(status="queued", id=PROVIDER_ID, content=ORIG)
get(id)  [t < 60s] → Memory(status="done",   id=PROVIDER_ID, content=PROVIDER_VALUE)
                     (with x-mem0.original_content=ORIG when applicable)
get(id)  [t = 60s] → ProviderError(code="ingestion_timeout")
```

---

## 4. Provider-id ↔ OMP-id mapping

| Provider     | Native id format             | OMP `Memory.id` format                          |
|--------------|------------------------------|-------------------------------------------------|
| postgres     | UUID                         | UUID (unchanged)                                |
| passthrough  | upstream-defined             | mirrored (unchanged)                            |
| mem0         | UUID                         | UUID (used as-is)                               |
| supermemory  | 22-char base62 (e.g. `JSPuxDxbavarZnVZLk5Ai8`) | used as-is                       |
| letta        | `passage-{uuid}` scoped to `agent-{uuid}` | `mem_{agent_id}_{first_passage_id}` (M2 invariant carried forward) |

Letta-only invariant: `x-letta.passage_ids: list[str]` lists ALL passage
ids created by `add()` (auto-chunking can produce N>1). `delete(id)`
deletes every entry in that list.

**Letta `Memory.content` asymmetry**: `add()` returns the **user-supplied
original text** as `Memory.content` (research.md R5). `list()` and
`search()` return the **upstream chunk text** as `Memory.content`,
because Letta exposes only chunks once `add` has returned — the original
is not recoverable from a chunk. Callers needing original content MUST
supply their own storage (or use a synchronous provider).

**Cleanup ordering**: live-mode fixtures MUST delete passages BEFORE
deleting agents (passages are scoped to an agent; agent deletion may
orphan or fail-fast on contained passages depending on upstream
behaviour).

---

## 4a. Live-mode env-var handling (binds FR-118)

The live-mode opt-in MUST treat env vars as untrusted input:

- `OMP_LIVE` activates live mode iff its stripped value (after
  `.strip()`) equals exactly `"1"`. Any other value (`"true"`,
  `"yes"`, `"0"`, empty, whitespace-only) MUST keep mock mode active.
- `*_API_KEY` env vars activate the matching provider iff their
  stripped value is non-empty AFTER `.strip()`. Whitespace-only or
  empty values MUST keep that provider in mock mode (no half-configured
  states that would silently exfiltrate test data with a malformed key).
- Env-var names are matched **case-sensitively**: `MEM0_API_KEY`,
  `SUPERMEMORY_API_KEY`, `LETTA_API_KEY`, `OMP_LIVE`, `OMP_INGEST_TIMEOUT`.
- API-key values MUST NEVER be logged. The conftest fixture MUST log
  only `"mem0 live mode enabled"` (not the key value, not even a prefix).
- `OMP_INGEST_TIMEOUT` MUST be parsed as a positive integer; values
  `<= 0`, non-numeric, or `> 600` (10 min hard cap to prevent
  runaway-poll DoS against test infra) MUST be rejected with a clear
  warning AND fall back to the default 60.

---

## 5. Live-mode fixture (test infrastructure)

Definition (in `sdk-python/tests/conftest.py`):

```python
@pytest.fixture
def mem0_adapter(request):
    if os.environ.get("OMP_LIVE") == "1" and os.environ.get("MEM0_API_KEY"):
        from openmem.adapters.mem0 import Mem0Adapter
        adapter = Mem0Adapter(api_key=os.environ["MEM0_API_KEY"])
        created_ids: list[str] = []
        # patch adapter.add to record ids ...
        def cleanup():
            for mid in created_ids:
                try:
                    adapter.delete(mid)
                except Exception as exc:
                    logger.warning("mem0 cleanup failed for %s: %s", mid, exc)
        request.addfinalizer(cleanup)
        return adapter
    # mock-mode fallback: M2's PostgresAdapter shim, unchanged
    return _postgres_shim_for("mem0")
```

Same shape for `supermemory_adapter` and `letta_adapter` (the latter also
tracks `created_agent_ids` for its agent-cache cleanup).

The `adapter` parametrize entries in `test_contract_*.py` are unchanged;
the live/mock decision lives entirely behind the fixture name.

---

## 6. Pytest marker

`@pytest.mark.live` — registered in `pyproject.toml` (or `conftest.py`)
under `markers`. Used for:

- Tests that explicitly assert async-ingestion semantics (e.g.
  `test_mem0_get_raises_ingestion_timeout`), which only make sense in
  live mode.
- The CI nightly job runs `pytest -m live`; PR runs use
  `pytest -m "not live"` (default). The collection-time hook in conftest
  also skips `@pytest.mark.live` tests when `OMP_LIVE != "1"`.
