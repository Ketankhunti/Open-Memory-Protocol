# Contract — OMP ↔ letta-client 1.10 mapping (M2.1)

**Feature**: `003-m2-1-live` | **Supersedes**: `specs/002-m2-pool-passthrough-adapters/contracts/letta-mapping.md`
**Upstream**: `letta-client>=1.10` (`Letta(api_key=..., base_url=...)`).
M2 used `token=...`; that argument is gone in 1.10 — adapter MUST use
`api_key=`.

OMP-id format (carried forward from M2): `mem_{agent_id}_{first_passage_id}`.

| OMP verb         | Letta SDK call                                                              | Request shape                                                          | Response handling                                                                                                                                                              | Error translation |
|------------------|------------------------------------------------------------------------------|------------------------------------------------------------------------|--------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|-------------------|
| `add(content, user_id, **)` | `_agent_for(user_id)` (cached) → `client.agents.passages.create(agent_id, text=content)` | text kwarg (LLM auto-chunks long text)                                 | Returns `list[Passage]`. Adapter takes `passages[0].id` as canonical, encodes `id = f"mem_{agent_id}_{passages[0].id}"`, sets `Memory(content=ORIGINAL, status="done", x-letta={"agent_id":agent_id, "passage_ids":[p.id for p in passages]})`. | `4xx` → `InvalidRequestError`; `401` → `AuthError`; `429` → `RateLimitedError`; `5xx` → `ProviderError(provider="letta")` |
| `get(id)`        | NOT IMPLEMENTED (Letta has no `passages.retrieve`)                           | n/a                                                                    | `capabilities().verbs` excludes `get`; calling raises `UnsupportedCapabilityError` BEFORE any network call (FR-116).                                                           | n/a |
| `list(user_id, limit, cursor)` | `_agent_for(user_id)` → `client.agents.passages.list(agent_id, limit=limit)` | limit kwarg                                                            | Returns `list[Passage]`. Map each → `Memory(id=f"mem_{agent_id}_{p.id}", content=p.text, status="done", created_at=p.created_at, x-letta={"agent_id":agent_id, "passage_ids":[p.id]})`. `next_cursor=None` (Letta has no cursor). | Same as add |
| `search(query, user_id, limit, **)` | `_agent_for(user_id)` → `client.agents.passages.search(agent_id, query=query, top_k=limit)` | **`top_k=limit`** (NOT `limit=`). Tag filtering is OUT OF SCOPE for M2.1 — OMP `search` does not accept `tags` (deferred to a future spec). | Returns `PassageSearchResponse(count, results=[Result(id, content, timestamp, tags)])`. Map → `[SearchResult(memory=Memory(id=f"mem_{agent_id}_{r.id}", content=r.content, status="done"), score=None)]`. Letta's response carries no score; OMP score is `None` (back-compat). | Same as add |
| `update(id, content)` | NOT IMPLEMENTED (no upstream verb)                                  | n/a                                                                    | `capabilities().verbs` excludes `update`; calling raises `UnsupportedCapabilityError`.                                                                                          | n/a |
| `delete(id)`     | parse `id` → `(agent_id, _)`; for each `pid` in `Memory.x-letta.passage_ids` (or just the parsed passage id if no record cached): `client.agents.passages.delete(agent_id, <pid_kwarg>=pid)` | `<pid_kwarg>` runtime-introspected from `passages.delete.__signature__` (M2 used `passage_id=`, live API rejected it; adapter MUST adapt) | Returns `{"message":"OK"}` per passage; adapter returns `None` once at least one delete succeeds. Per-passage failures logged at WARNING. | Same as add |
| `capabilities()` | static                                                                       | n/a                                                                    | `Capabilities(verbs=["add","list","search","delete","capabilities"], features={"status_field": True, "async_ingestion": False, "auto_chunking": True})`. NO `get`, NO `update`. | n/a |

Key invariants:

- **`api_key=` not `token=`** in `Letta(...)` constructor (M2 wrap-up fix).
- **Auto-chunking is provider-internal**: OMP returns ONE memory per `add()`;
  all chunk passage ids ride under `x-letta.passage_ids` and are deleted
  together (FR-114).
- **`top_k=` not `limit=`** in `passages.search` (FR-115). Tag filtering
  is deferred (no `tags=` argument; future spec).
- **Capability-aware skip mechanism (M2)** transparently handles the
  missing `get`/`update` verbs — `test_contract_lifecycle.py` is NOT
  edited (Principle II / SC-109).
- **Agent cache `_agent_for(user_id)`** persists across the adapter's
  lifetime; on agent-not-found, the cache entry is invalidated and
  re-created on next call (FR-117).
