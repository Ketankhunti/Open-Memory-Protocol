# Contract — OMP ↔ mem0ai 2.x mapping (M2.1)

**Feature**: `003-m2-1-live` | **Supersedes**: `specs/002-m2-pool-passthrough-adapters/contracts/mem0-mapping.md`
**Upstream**: `mem0ai>=2.0,<3` (`MemoryClient`, host default `https://api.mem0.ai`)

All calls are issued via the synchronous `MemoryClient`. Imports stay
lazy (M2 invariant).

| OMP verb         | Mem0 SDK call                                                                                  | Request shape                                                                                  | Response handling                                                                                                                | Error translation |
|------------------|-----------------------------------------------------------------------------------------------|------------------------------------------------------------------------------------------------|----------------------------------------------------------------------------------------------------------------------------------|-------------------|
| `add(content, user_id, **)` | `client.add(messages=[{"role":"user","content":content}], user_id=user_id, metadata=...)` | `{"messages":[{"role":"user","content":...}], "user_id":..., "metadata":{scope, tags, ...}}` | Returns `{"message", "status":"PENDING", "event_id"}`. Adapter constructs `Memory(id=event_id, content=ORIGINAL, status="queued", x-mem0={"event_id":..., "original_content":ORIGINAL})`. | mem0 `400` → `InvalidRequestError`; `401` → `AuthError`; `429` → `RateLimitedError`; other → `ProviderError(provider="mem0")` |
| `get(id)`        | poll loop: `client.get(memory_id=id)` every 1 s, exponential cap at 5 s, total budget = `OMP_INGEST_TIMEOUT` (default 60 s) | `{memory_id: id}`                                                                              | First non-404 response → `Memory(id=id, content=resp["memory"], status="done", x-mem0.original_content=ORIGINAL_if_known, ...)` | 404 across full budget → `ProviderError(code="ingestion_timeout", provider="mem0", details={"event_id":id, "elapsed":...})` |
| `list(user_id, limit, cursor)` | `client.get_all(filters={"user_id": user_id}, version="v2", page=N, limit=limit)` where N decoded from cursor | filters dict + page/limit kwargs                                                                | Parse `{"count","next","previous","results":[Memory]}`. OMP `next_cursor` opaquely encodes `page+1` when `next` is non-null; else `None`. | Same as add |
| `search(query, user_id, limit, **)` | `client.search(query=query, filters={"user_id": user_id}, version="v2", limit=limit)` | `{query, filters:{user_id}, version:"v2", limit}`                                              | Parse `{"results":[Memory + score]}` → `[SearchResult(memory=Memory(...), score=item["score"])]`. Memory.content is the (rewritten) `memory` field; `x-mem0.original_content` left unset for results we did not create. | Same as add |
| `update(id, content)` | `client.update(memory_id=id, data=content)` | `{memory_id, data}` | Returns updated dict; reshape into `Memory(status="done", ...)` | Same as add |
| `delete(id)`     | `client.delete(memory_id=id)`                                                                  | `{memory_id}`                                                                                  | Returns `{"message":"OK"}` (or similar); adapter returns `None`. Not-found → no error (idempotent).                              | Same as add |
| `capabilities()` | static                                                                                         | n/a                                                                                            | `Capabilities(verbs=["add","get","list","search","update","delete","capabilities"], features={"status_field": True, "async_ingestion": True})`. NO `context`, NO `audit`. | n/a |

Key invariants:

- **No SDK call inside `__init__`**; `MemoryClient` constructed lazily on
  first verb invocation (M2 lazy-import + lazy-init invariant carried
  forward).
- **`Memory.user_id` is read from upstream `user_id` field** (mem0
  preserves it); when ambiguous, fall back to the `user_id` argument
  passed to the call.
- **Rewritten content is preserved**: the LLM-rewrite is mem0's product
  feature; the adapter MUST surface it as `Memory.content`. The original
  ride-along is `x-mem0.original_content` on records the SDK created (we
  cannot reconstruct the original for records added by other clients).
- **Contract test impact**: `test_add_then_search_finds_original_content`
  passes via mem0's semantic search recognising the original phrase
  inside the rewritten content. If a future mem0 release rewrites away
  every trace of the original, the adapter falls back to substring search
  over `x-mem0.original_content` (TODO if observed).
