# Phase 0 — Research: M2.1 Live-API bridges

**Feature**: `003-m2-1-live` | **Date**: 2026-04-28
**Inputs**: [spec.md](spec.md), `/memories/repo/m2.1-live-api-findings.md`

All NEEDS CLARIFICATION items in the plan's Technical Context are resolved
here. Decisions are recorded in the Decision / Rationale / Alternatives
format prescribed by the speckit.plan workflow.

---

## R1. Async-ingestion contract

**Decision**: `add()` returns immediately with `Memory(status="queued",
id=<provider id>, content=<original>)`. `get(id)` polls the provider with a
bounded budget (default 60 s, env `OMP_INGEST_TIMEOUT`); on timeout it raises
`ProviderError(code="ingestion_timeout", provider=<name>, details={"event_id": ...})`.

**Rationale**: Preserves OMP's "add returns the canonical record" surface
without lying about durability. Polling is bounded so callers never hang
indefinitely. The `status` enum (FR-122) makes asynchrony observable and
opt-in for advanced callers.

**Alternatives considered**:

- *Block inside `add()` until `done`* — rejected: forces every caller to pay
  the 25 s mem0 ingestion latency synchronously, even when they only need
  fire-and-forget semantics.
- *Fire-and-forget — no `id` returned* — rejected: breaks the contract
  test suite (it round-trips on `id`) and leaves the user with no way to
  reference the memory.
- *Background polling thread inside the adapter* — rejected: introduces
  threading and lifecycle bugs; bounded sync poll inside `get()` is simpler
  and matches the existing single-threaded contract surface.

---

## R2. LLM-rewrite preservation (mem0)

**Decision**: When a provider rewrites stored content (mem0's LLM
extraction pipeline does this), the adapter MUST stash the user-supplied
text under `x-{provider}.original_content` and set `Memory.content` to the
provider's rewritten value. A new contract test
`test_add_then_search_finds_original_content` asserts that searching for
the original phrase still returns the memory.

**Rationale**: Two equally important invariants. (a) `Memory.content` must
reflect what the provider actually stored — otherwise `get(id).content`
would diverge from the provider's own UI. (b) The user's intent ("I added
'omp probe XYZ'; I should be able to find it back") must survive any
provider-side transformation. Search bridges the two: if mem0's semantic
search finds the rewritten record from the original phrase, the user's
intent is preserved. If it doesn't, the contract test fails and we need
to fall back to substring search over `x-mem0.original_content`.

**Alternatives considered**:

- *Refuse to ingest if mem0 rewrites* — rejected: mem0's rewrite is the
  product. We cannot disable it.
- *Return both fields concatenated* — rejected: violates Principle V (no
  reinterpretation of standard fields).
- *Refuse to advertise `search` for mem0* — rejected: search is mem0's
  primary verb; downgrading it makes the adapter useless.

---

## R3. Live-vs-mock test switch

**Decision**: Live mode is opted into by setting BOTH `OMP_LIVE=1` AND the
matching `*_API_KEY` environment variable. Per-provider granularity: setting
only `MEM0_API_KEY` runs mem0 live and keeps supermemory + letta in mock
mode. The conftest shim that swapped in `PostgresAdapter` for absent
providers (M2) is preserved as the mock-mode default.

**Rationale**: A single global flag would force users to set all three keys
or none, burning quota unnecessarily and complicating CI. Per-provider
granularity also lets a maintainer reproduce one provider's bug in
isolation. The `OMP_LIVE` master switch prevents accidental key-bearing
local runs (e.g. on a contributor's machine where keys leaked from another
project).

**Alternatives considered**:

- *Single `OMP_LIVE=mem0,letta` flag* — rejected: more ergonomics for the
  flag, less for the env-vars story; users still need to set keys, so
  having keys ALONE not be enough is a desirable safety net.
- *Separate test tree (`tests_live/`)* — rejected: violates Principle II
  (would necessitate a parallel contract suite). Live mode plugging into
  the same fixture matrix is the whole point.
- *No switch — live mode whenever a key is present* — rejected: too easy
  to nuke a contributor's quota with `pytest`.

---

## R4. Live-test cleanup strategy

**Decision**: Each live-mode fixture maintains a per-test list of created
memory ids. A pytest `request.addfinalizer` callback iterates the list at
teardown and calls `adapter.delete(id)` for each one. Failures are logged
at WARNING level with the provider id and full traceback; tests are NOT
failed by cleanup errors (EC-105).

For Letta specifically, the finalizer also deletes any agent created
during the test (`agents.delete(agent_id)`).

**Rationale**: Per-test cleanup keeps state local and parallelisable; a
suite-level teardown would require either a global registry (race-prone)
or a dedicated cleanup CLI (out of band). Treating cleanup failures as
warnings prevents flaky-network noise from blocking unrelated PRs.

**Alternatives considered**:

- *Suite-level teardown via `conftest.fixture(scope="session")`* —
  rejected: makes test parallelisation hazardous; a flake in one test
  can leak state into others.
- *Dedicated `make clean-live` CLI* — rejected: shifts cleanup outside
  the test contract, easy to forget; first failure leaks state for ever.
- *Tag created memories with a UUID prefix and sweep by prefix later* —
  rejected: adds wire complexity, still needs a sweep job, and breaks
  the mem0 LLM-rewrite invariant (the prefix may be rewritten away).

---

## R5. Letta auto-chunking handling

**Decision**: When `passages.create(agent_id, text=...)` returns
`list[Passage]` of length N>1, the adapter:

1. Constructs a single OMP `Memory` whose `id` is
   `mem_{agent_id}_{first_passage_id}`.
2. Records *all* passage ids under `x-letta.passage_ids`.
3. Sets `Memory.content` to the user-supplied original text (NOT the
   first chunk).
4. On `delete(id)`, fetches `x-letta.passage_ids` and deletes every
   passage; on partial failure, returns success once *any* passage was
   removed and logs the rest.

**Rationale**: One-OMP-memory-per-chunk would (a) explode quotas, (b)
break round-trip identity (`add` returns one id, the user uses one id),
and (c) make `list()` results bewildering. Tracking all chunk ids
under an `x-` field keeps the standard-field semantics intact (Principle V).

**Alternatives considered**:

- *One OMP memory per chunk* — rejected: violates round-trip identity.
- *Refuse to ingest content longer than Letta's chunk threshold* —
  rejected: arbitrary cap, surfaces a Letta-specific limit to all OMP
  callers.
- *Concatenate chunk ids with a separator inside the canonical id* —
  rejected: `id` is supposed to be opaque; baking provider details into
  it leaks abstraction.

---

## R6. Spec evolution sequencing (FR-122)

**Decision**: `Memory.status` is added to `spec/omp-0.1.openapi.yaml`
**before** any adapter code change. Order:

1. Edit OpenAPI: add `status` enum to `Memory` schema (optional, no default).
2. Update `openmem.types.Memory` to mirror: `status: Optional[Literal[...]] = None`.
3. Update `test_types_match_openapi.py` to assert the round-trip.
4. Per-adapter changes: emit `status` (or pass through upstream value).
5. Add `code="ingestion_timeout"` to `OMPError.code` enum in OpenAPI;
   register the corresponding `ProviderError` constructor.

**Rationale**: Principle I is NON-NEGOTIABLE: spec leads, code follows.
Adding the field to Pydantic without amending the OpenAPI would create
the very drift the principle exists to prevent.

**Alternatives considered**:

- *Add `status` only to Pydantic, leave OpenAPI alone* — rejected:
  violates Principle I.
- *Bake the poll inside `add()` so `status` is unnecessary* — already
  rejected in R1; bringing it back would mask the asynchrony from the
  wire spec, which is exactly what other implementers need to know.

---

## R7. Per-provider live-mode upstream surface (reference)

These are the canonical empirical findings (probed 2026-04-28 with valid
keys) that the contracts/*.md files in Phase 1 will turn into per-verb
mappings.

### mem0ai 2.0.1
- `MemoryClient(api_key=..., host="https://api.mem0.ai")`.
- `add(messages=[{"role":"user","content":...}], user_id=...)` →
  `{"message", "status":"PENDING", "event_id"}` (async, no id).
- `get(memory_id)` → full record once ingested; `KeyError`-style 404 until then.
- `get_all(filters={"user_id": ...}, version="v2", page=N, limit=L)` →
  `{"count","next","previous","results":[Memory]}`.
- `search(query=..., filters={"user_id": ...}, version="v2", limit=L)` →
  `{"results":[Memory + score]}`.
- Memory shape: `{id, memory, user_id, agent_id, app_id, run_id,
  metadata, categories, created_at, updated_at, expiration_date,
  structured_attributes, score?}`.
- Content is LLM-rewritten in ingestion.

### supermemory REST `https://api.supermemory.ai/v3`
- Auth header: `Authorization: Bearer <key>`.
- `POST /memories` → `{"id","status":"queued"}` (async).
- `GET /memories/{id}` → full doc (camelCase: `connectionId, createdAt,
  updatedAt, customId, ogImage, taskType, containerTags, spatialPoint, userId`).
- `POST /memories/list` body `{"limit","page","filters":{"user_id":...}}`
  → `{"memories":[...], "pagination":{"currentPage","limit",...}}`.
- `POST /search` body `{"q","limit"}` → `{"results":[{chunks:[{content,score,...}],
  documentId, score, title, ...}], "timing", "total"}`.
- Provider-assigned `userId` is metadata; tenant `user_id` rides in
  `metadata.user_id`.
- No native `update`.

### letta-client 1.10.3
- `Letta(api_key=..., base_url=...)`.
- `agents.passages` surface: `create / delete / list / search` —
  **no `retrieve`**.
- `passages.create(agent_id, text=...)` → `list[Passage]` (auto-chunked).
- `passages.list(agent_id, limit=N)` → `list[Passage]` (no cursor).
- `passages.search(agent_id, query=..., top_k=N, tags=..., tag_match_mode=...)`
  → `PassageSearchResponse(count, results=[Result(id, content, timestamp, tags)])`.
- `passages.delete` kwarg name needs runtime introspection; M2 used
  `passage_id=` and the live API rejected it. Phase-1 contract pins the
  correct call shape.

---

**Phase 0 status**: ✅ Complete. All NEEDS CLARIFICATION resolved.
Proceed to Phase 1.
