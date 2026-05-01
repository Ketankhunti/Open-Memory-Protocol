# Feature Specification: M2.1 — Live-API bridges for translation adapters

**Feature Branch**: `003-m2-1-live`
**Created**: 2026-04-28
**Status**: Draft
**Input**: User description: "M2.1: rebuild the Mem0/Supermemory/Letta translation adapters against today's real public APIs (mem0ai 2.x, supermemory v3, letta-client 1.10) so that `python examples/02_switch_providers.py` and the parametrized contract suite both pass against live providers when API keys are present."

## Background

M2 (feature `002-m2-pool-passthrough-adapters`) shipped three translation
adapters whose unit + contract tests pass in **mock mode** (158 passed, 2
skipped). When the same adapters were run against the providers' live
public APIs with valid keys, the integration broke for all three:

| Provider | Live-mode failure observed in M2 |
|----------|----------------------------------|
| mem0     | `mem0ai 2.0.1` `add()` is async — returns `{message, status, event_id}` with no `id`; `get_all` requires `filters={"user_id": ...}` and `version="v2"`. |
| supermemory | Real base URL is `/v3` (M2 used `/v1`). `POST /memories` returns `{"id", "status":"queued"}` and ingestion is asynchronous. Listing is `POST /memories/list`, search is `POST /search`. Document fields are camelCase. |
| letta    | `Letta(token=...)` → `Letta(api_key=...)` (fixed in M2 wrap-up). `agents.passages` has no `retrieve` method, so OMP `get(id)` cannot map directly. `passages.create` returns a *list* of auto-chunked `Passage` objects. `passages.search` uses `top_k=` not `limit=`. |

The M2 contract tests still pass because the conftest fixtures use
PostgresAdapter-backed shims that *imitate* the providers — they do not
exercise the wire / SDK boundary. M2.1 closes that gap.

Empirical findings (probed 2026-04-28 with valid API keys) are stored in
`/memories/repo/m2.1-live-api-findings.md` and reproduced inline below
where they drive a requirement.

## Clarifications

### Session 2026-04-28

- Q: How should adapters reconcile OMP's "`add()` returns the canonical record" contract with mem0/supermemory's asynchronous ingestion? → A: Adapters MUST return immediately with a `Memory` whose `id` is the provider's accepted-id and `status="queued"`; `get(id)` MAY block (with a documented bounded poll) until the record is `done` or the poll budget elapses, then raise `ProviderError` with `code="ingestion_timeout"`. The OMP wire spec adds an optional `status` enum (`queued|indexing|done|failed`) that synchronous providers (postgres, passthrough-against-postgres) report as `done`.
- Q: How do live tests run in CI without leaking real provider state and burning quota? → A: Live tests are gated by env vars (`OMP_LIVE=1` AND the matching `*_API_KEY`); default `pytest` runs continue to use mock-mode shims unchanged. CI runs live mode in a dedicated nightly job; PR runs do not.
- Q: How should adapters handle providers that LLM-rewrite stored content (mem0)? → A: Adapters MUST preserve the **original** content the user passed under `x-{provider}.original_content` and surface the provider's rewritten text as `Memory.content`. A new contract test `test_add_then_search_finds_original_content` asserts that searching for the original phrase returns the memory regardless of any rewrite the provider performed.

## User Scenarios & Testing *(mandatory)*

### User Story 1 — Mem0 works end-to-end against the live API (Priority: P1) 🎯 MVP

A user installs `openmem[mem0]`, sets `MEM0_API_KEY`, runs `python examples/02_switch_providers.py`, and sees comparable output for `postgres` and `mem0`. The contract suite, when invoked as `OMP_LIVE=1 MEM0_API_KEY=… pytest -k mem0`, passes for every verb mem0 advertises in its capability matrix.

**Why this priority**: Mem0 is the most popular external memory provider in the OMP target audience, and the M2 commitment explicitly lists it. Until mem0 works against the real API, M2 has no end-to-end story.

**Independent Test**: With a valid `MEM0_API_KEY` set: `OMP_LIVE=1 pytest "sdk-python/tests/test_contract_search.py::test_add_then_search_finds_original_content[mem0]"` passes (this exercises the async-ingestion + LLM-rewrite path). The example demo prints results from `mem0` alongside `postgres`.

**Acceptance Scenarios**:

1. **Given** valid `MEM0_API_KEY`, **When** `mem.add(content="omp probe XYZ", user_id="u1")` is called, **Then** the call returns within 5 s with a `Memory` whose `id` matches the provider's id and whose `status == "queued"` (or `"done"` if ingestion completed inside the SDK call).
2. **Given** a memory whose ingestion is queued, **When** `mem.get(id)` is called, **Then** the SDK polls up to a documented bounded budget (default 60 s) and returns the materialised `Memory` once `status == "done"`, or raises `ProviderError(code="ingestion_timeout")` after the budget.
3. **Given** mem0 has rewritten the stored content via its LLM pipeline, **When** the user calls `mem.search("omp probe XYZ", "u1")`, **Then** at least one result references the original phrase (matched against `x-mem0.original_content` or via mem0's own semantic search), and `result.memory.content` carries the provider's rewritten text.
4. **Given** `mem.list("u1")` is called against a user with >1 page of memories, **When** the SDK paginates, **Then** the call uses `get_all(filters={"user_id": "u1"}, version="v2", page=N)` and the SDK encodes the page number inside the OMP `next_cursor` opaquely.

---

### User Story 2 — Supermemory works end-to-end against the live API (Priority: P2)

A user installs the SDK, sets `SUPERMEMORY_API_KEY`, and the same demo + contract suite work for the `supermemory` provider against `https://api.supermemory.ai/v3`.

**Why this priority**: Supermemory is the second OMP commitment. Its API surface is the most divergent (camelCase fields, `POST` for list/search, `userId` provider-assigned + tenant `user_id` in metadata).

**Independent Test**: With `SUPERMEMORY_API_KEY` set: `OMP_LIVE=1 pytest -k supermemory` passes for all advertised verbs; the example prints `supermemory` results.

**Acceptance Scenarios**:

1. **Given** `SupermemoryAdapter(api_key=...)` with no explicit `base_url`, **When** any verb is called, **Then** the request goes to `https://api.supermemory.ai/v3` (the default updates from the M2 `/v1` value).
2. **Given** `mem.add(content="...", user_id="u1")`, **When** invoked, **Then** the SDK posts `{"content", "metadata": {"user_id": "u1"}}` to `POST /memories`, and the returned `Memory` carries the queued `id` plus `status="queued"`.
3. **Given** a list call, **When** invoked, **Then** the SDK calls `POST /memories/list` with `{"limit", "page", "filters": {"user_id":"u1"}}` and parses the camelCase response (`memories[]`, `pagination.currentPage`, `pagination.limit`) into the OMP `MemoryPage`.
4. **Given** a search call, **When** invoked, **Then** the SDK calls `POST /search` with `{"q", "limit"}` and maps the chunk-shaped response (`results[].chunks[].score` plus `results[].documentId`) into OMP `SearchResult` objects.
5. **Given** any verb raises an upstream error, **Then** it is translated to the matching `OMPError` subclass (FR-014 carry-over).

---

### User Story 3 — Letta works end-to-end against the live API (Priority: P3)

A user installs `openmem[letta]`, sets `LETTA_API_KEY`, and the demo + contract suite work for the `letta` provider.

**Why this priority**: Letta is the third commitment. It has the smallest verb surface (no native `get` or `update`), so this story confirms the capability-aware skip mechanism (already in conftest) handles a real provider that legitimately lacks verbs.

**Independent Test**: With `LETTA_API_KEY` set: `OMP_LIVE=1 pytest -k letta` passes for `add / list / search / delete / context`; `get` and `update` are skipped (capability-aware). Example prints `letta` results.

**Acceptance Scenarios**:

1. **Given** `LettaAdapter(api_key=...)`, **When** any verb is called, **Then** the underlying client is constructed as `Letta(api_key=api_key)` (not `token=`).
2. **Given** `mem.add(content="long text", user_id="u1")` where Letta auto-chunks the text into multiple passages, **When** `add` returns, **Then** the SDK returns one `Memory` whose `id` is encoded as `mem_{agent_id}_{first_passage_id}` and whose `x-letta.passage_ids` field lists *all* created passage ids.
3. **Given** the user calls `mem.get(id)`, **When** Letta has no `passages.retrieve` method, **Then** the adapter does **not** advertise `get` in `capabilities().verbs`; calling `get` raises `UnsupportedCapabilityError` *without* hitting the network. (Equivalent to FR-009.)
4. **Given** the user calls `mem.search(query, "u1", limit=5)`, **When** invoked, **Then** the SDK calls `agents.passages.search(agent_id, query=query, top_k=5)` (mapping `limit→top_k`) and parses the `PassageSearchResponse` into OMP `SearchResult` objects.

---

### User Story 4 — Live & mock modes coexist in the test suite (Priority: P2)

A contributor runs `pytest sdk-python/tests` on their laptop with no API keys set; the suite still passes 100% in mock mode (M2's behaviour is preserved). When they set `OMP_LIVE=1` plus one or more `*_API_KEY` env vars, only those provider parametrize entries switch to live mode; the rest stay mock.

**Why this priority**: Without this, M2.1 would either break local dev (force keys) or burn quota silently. The on-by-default policy is mock-mode.

**Independent Test**: `pytest sdk-python/tests --no-cov` with no env vars set: 158 passed / 2 skipped (M2 baseline preserved). Same command with `OMP_LIVE=1 MEM0_API_KEY=...`: mem0 entries run live, supermemory + letta stay mock.

**Acceptance Scenarios**:

1. **Given** no `OMP_LIVE` env var, **When** `pytest` runs, **Then** the existing PostgresAdapter-backed shims are used for mem0/supermemory/letta (M2 mock mode preserved).
2. **Given** `OMP_LIVE=1` and `MEM0_API_KEY=...` are set, **When** `pytest` runs, **Then** the `mem0_adapter` fixture constructs a real `Mem0Adapter` and the supermemory + letta fixtures stay in mock mode (because their keys are absent).
3. **Given** any provider runs in live mode, **When** its tests complete, **Then** all created memories on the provider are deleted in a `finalizer` so subsequent runs start clean.

---

### Edge Cases

- **EC-101 (mem0 ingestion timeout)**: When mem0 keeps a memory in `PENDING` longer than the configured poll budget, `get()` raises `ProviderError(code="ingestion_timeout", provider="mem0")` with the `event_id` preserved in `details`.
- **EC-102 (mem0 LLM rewrites empty out content)**: When mem0's LLM pipeline rejects a memory (extraction returns empty), the next `get_all` shows zero results. The adapter MUST treat this as a successful no-op `add` rather than retrying — the contract test exercising this case asserts `len(list().items) == 0` and does NOT raise.
- **EC-103 (supermemory `userId` ≠ tenant user_id)**: The provider-assigned `userId` in the response is metadata-only; the SDK MUST ignore it for OMP `Memory.user_id` and instead read `metadata.user_id`.
- **EC-104 (letta auto-chunking)**: When `add(content=long_text)` produces multiple passages, the SDK MUST report a single `Memory` (the first passage's id) but record all passage ids under `x-letta.passage_ids` so a subsequent `delete` removes all of them.
- **EC-105 (live test cleanup failure)**: If a finalizer fails to delete a remote memory (e.g. provider 5xx), the suite MUST log a warning with the provider id, mark the test as PASSED (do not pollute red builds with cleanup noise), and continue.
- **EC-106 (provider-side rate limit during live test)**: When a 429 is returned mid-suite, the SDK MUST raise `RateLimitedError` (already done) and the test framework MUST honour any `Retry-After` header by sleeping then retrying once before failing the test.

## Requirements *(mandatory)*

### Functional Requirements

#### Mem0 live integration (US1)

- **FR-101**: `Mem0Adapter` MUST construct `MemoryClient(api_key=..., host=...)` against `mem0ai>=2.0`. Imports MUST remain lazy (M2 invariant).
- **FR-102**: `add()` MUST persist via `client.add(messages=[{"role":"user","content":...}], user_id=...)`, capture the returned `event_id`, and return a `Memory` with `id=event_id_or_provider_id`, `status="queued"`, `content=<original>`, `x-mem0={"event_id": ..., "original_content": ...}`. (See clarified async contract.)
- **FR-103**: `list()` MUST call `client.get_all(filters={"user_id": ...}, version="v2", page=N, limit=L)` and parse `{"results": [...], "next": ..., "previous": ..., "count": ...}`. The OMP `next_cursor` MUST opaquely encode `page+1` when `next` is non-null.
- **FR-104**: `search()` MUST call `client.search(query=..., filters={"user_id": ...}, version="v2", limit=...)`, parse `{"results": [...]}`, and emit OMP `SearchResult` with `score=item["score"]`. `user_id` is REQUIRED — if absent, the adapter MUST raise `InvalidRequestError(message="user_id is required")` BEFORE issuing any upstream call (defence in depth: prevents unscoped tenant-leak queries).
- **FR-105**: `get(id)` MUST poll `client.get(memory_id=id)` for up to `OMP_INGEST_TIMEOUT` seconds (default 60), backing off, returning the materialised `Memory` once present, or raising `ProviderError(code="ingestion_timeout")`.

#### Supermemory live integration (US2)

- **FR-106**: `SupermemoryAdapter` default `base_url` MUST be `https://api.supermemory.ai/v3` (overridable via constructor or `SUPERMEMORY_BASE_URL` env var).
- **FR-107**: `add()` MUST `POST /memories` with body `{"content": ..., "metadata": {"user_id": ..., "scope": ..., "tags": ..., "x-...": ...}}` and parse `{"id", "status"}` into a queued `Memory`.
- **FR-108**: `list()` MUST `POST /memories/list` with body `{"limit", "page", "filters": {"user_id": ...}}` and parse the camelCase document shape (`memories[].createdAt`, `memories[].metadata`, `pagination.currentPage`, `pagination.limit`) into `MemoryPage`. The OMP `next_cursor` MUST opaquely encode `currentPage+1` when more pages exist.
- **FR-109**: `search()` MUST `POST /search` with body `{"q", "limit"}` and parse the chunk-shaped response into OMP `SearchResult` (one `SearchResult` per `documentId`, score = best chunk score). `user_id` is REQUIRED — if absent, the adapter MUST raise `InvalidRequestError(message="user_id is required")` BEFORE issuing any HTTP call (defence in depth: prevents cross-tenant search results).
- **FR-110**: `get(id)` MUST `GET /memories/{id}` and parse the camelCase doc; provider-assigned `userId` MUST be ignored — `Memory.user_id` is read from `metadata.user_id`.
- **FR-111**: `update` MUST NOT be advertised in `capabilities().verbs` (carry-over from M2; the live API still has no public update endpoint).

#### Letta live integration (US3)

- **FR-112**: `LettaAdapter` MUST construct `Letta(api_key=api_key, base_url=...)` against `letta-client>=1.10`.
- **FR-113**: `add()` MUST call `client.agents.passages.create(agent_id, text=content)`, treat the returned value as a `list[Passage]`, take the first passage as the canonical OMP id, and stash *all* passage ids under `x-letta.passage_ids`.
- **FR-114**: `delete(id)` MUST delete every passage id recorded under the memory's `x-letta.passage_ids`, not just the canonical first one. On partial failure (some passage deletes succeed, others raise upstream), the adapter MUST return success once at least one passage was removed; per-passage failures MUST be logged at WARNING with the passage id and the upstream exception.
- **FR-115**: `search()` MUST call `client.agents.passages.search(agent_id, query=query, top_k=limit)` (note `top_k=`, NOT `limit=`) and parse `PassageSearchResponse(count, results=[Result(id, content, timestamp, tags)])`. Tag-based filtering is OUT OF SCOPE for M2.1 — the OMP `search` signature does not accept `tags`; a future spec MAY add it via `**kwargs` passthrough.
- **FR-116**: `capabilities().verbs` for Letta MUST exclude `get` and `update`. Calling either on the adapter MUST raise `UnsupportedCapabilityError` *before* any network call (FR-009 carry-over).
- **FR-117**: The `_agent_for(user_id)` cache MUST persist across the adapter's lifetime; first call creates the agent, subsequent calls reuse the cached id. On agent-not-found errors the cache entry MUST be invalidated.

#### Test infrastructure (US4)

- **FR-118**: `pytest` runs MUST default to mock mode (M2 behaviour). Live mode is opted into by setting `OMP_LIVE=1` *and* the matching `*_API_KEY`. Both values MUST be `.strip()`-ed; `OMP_LIVE` MUST equal exactly `"1"` (not `"true"`, `"yes"`, etc.); `*_API_KEY` MUST be non-empty after stripping. Whitespace-only or malformed values MUST keep the provider in mock mode. API-key values MUST NEVER be logged in any form (no prefixes, no lengths, no hashes that could enable credential confirmation).
- **FR-119**: Each live-mode fixture MUST register a `finalizer` that deletes every memory it created and (for Letta) every agent it spawned. Cleanup failures MUST be logged but MUST NOT fail the test.
- **FR-120**: A new contract test `test_add_then_search_finds_original_content` MUST be added; it asserts that searching for the original `add()` content returns the memory regardless of provider-side rewrites. This test runs for every advertised-search adapter.
- **FR-121**: A pytest marker `@pytest.mark.live` MUST be defined; live-only tests (e.g. ingestion-timeout coverage) MUST use it. CI runs the marker only in a nightly job, never on PRs.

#### Wire-spec evolution

- **FR-122**: `spec/omp-0.1.openapi.yaml` Memory schema MUST add an optional `status` enum (`queued | indexing | done | failed`). Synchronous providers report `done`; async providers report the upstream value. Pydantic `Memory` MUST gain an optional `status: Literal[...] | None` field, defaulting to `None` (back-compat with existing Memory payloads).

### Key Entities

- **Async-ingestion record**: A `Memory` returned by `add()` whose `status != "done"`. Callers MAY treat it as opaque until `get(id)` materialises the full record.
- **Provider id ↔ OMP id mapping**: Mem0 uses UUIDs (`62623fae-…`); Supermemory uses 22-char base62 ids (`JSPuxDxbavarZnVZLk5Ai8`); Letta uses `passage-{uuid}` scoped to `agent-{uuid}`. The Letta adapter still encodes its OMP id as `mem_{agent_id}_{passage_id}` (M2 invariant).
- **Live-mode fixture**: A `pytest` fixture that swaps the M2 PostgresAdapter-backed shim for a real provider client when `OMP_LIVE=1` and the credential env var are present. Owns a finalizer that cleans up remote state.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-101**: With `OMP_LIVE=1 MEM0_API_KEY=...`, the contract suite restricted to mem0 (`pytest -k mem0`) passes 100% for verbs mem0 advertises (skip rules unchanged from M2).
- **SC-102**: With `OMP_LIVE=1 SUPERMEMORY_API_KEY=...`, the contract suite restricted to supermemory passes 100% for advertised verbs.
- **SC-103**: With `OMP_LIVE=1 LETTA_API_KEY=...`, the contract suite restricted to letta passes 100% for advertised verbs (`get` and `update` are correctly skipped via the capability-aware hook).
- **SC-104**: With *no* env vars set, the full suite still produces 158 passed / 2 skipped (M2 mock-mode baseline preserved).
- **SC-105**: `python examples/02_switch_providers.py` with all three keys set prints non-empty `search` results for `postgres`, `mem0`, `supermemory`, and `letta` and exits 0.
- **SC-106**: A new contract test `test_add_then_search_finds_original_content` passes for every advertised-search adapter (postgres, passthrough, mem0, supermemory, letta) — i.e. searching for the original phrase still finds the memory after any provider-side rewrite.
- **SC-107**: After a full live-mode suite run, no test-created memories or agents remain on any provider (verified by counting before/after).
- **SC-108**: `Memory.status` is correctly populated (`queued | indexing | done | None`) by every adapter; the contract suite asserts `status` is preserved through `add → get → list → search`.
- **SC-109**: Adding a brand-new live-mode adapter to the matrix STILL requires zero edits to `test_contract_*.py` (M2 SC-005 carry-over).

## Assumptions

- `mem0ai>=2.0`, `supermemory` REST `/v3`, and `letta-client>=1.10` are the targeted upstream versions for M2.1. Older versions are out of scope.
- A "live" test run is permitted to take up to 60 s per test (the mem0 ingestion poll budget). The pytest-timeout default of 30 s MUST be raised to 90 s for live-marked tests.
- Provider rate limits are generous enough for one full contract-suite pass per hour. If they are not, US4 will need a `--live=mem0,letta` partial-mode flag (deferred).
- Test cleanup failures (network blips, provider 5xx) are logged warnings, not red builds.
- The OMP wire-spec change in FR-122 (`status` enum) is a *backwards-compatible additive* change — existing M1/M2 clients that never read `status` are unaffected.
- API keys are loaded from `.env` via the existing `examples/_env.py` and conftest auto-loader (M2-shipped).
