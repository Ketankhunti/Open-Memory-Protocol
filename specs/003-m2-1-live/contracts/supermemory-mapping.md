# Contract — OMP ↔ supermemory REST mapping (M2.1)

**Feature**: `003-m2-1-live` | **Supersedes**: `specs/002-m2-pool-passthrough-adapters/contracts/supermemory-mapping.md`
**Upstream**: `https://api.supermemory.ai/v3` (NOT `/v1`); auth via
`Authorization: Bearer <SUPERMEMORY_API_KEY>`. No SDK — direct `httpx.Client`.

Default `base_url` overridable via constructor or `SUPERMEMORY_BASE_URL` env.

| OMP verb         | HTTP call                                  | Request body                                                                            | Response handling                                                                                                                                                  | Error translation |
|------------------|--------------------------------------------|------------------------------------------------------------------------------------------|--------------------------------------------------------------------------------------------------------------------------------------------------------------------|-------------------|
| `add(content, user_id, **)` | `POST /memories`                | `{"content": content, "metadata": {"user_id": user_id, "scope": ..., "tags": ..., "x-...": ...}}` | Returns `{"id", "status":"queued"}`. Adapter constructs `Memory(id=resp["id"], content=ORIGINAL, status="queued", user_id=user_id, x-supermemory={"upstream_status":"queued"})`. | `400` → `InvalidRequestError`; `401` → `AuthError`; `429` → `RateLimitedError`; `5xx` → `ProviderError`; other → `ProviderError(provider="supermemory")` |
| `get(id)`        | `GET /memories/{id}` with bounded poll: 1 s/1 s/2 s/5 s/5 s/... up to `OMP_INGEST_TIMEOUT` budget | n/a                                                                                      | First non-404 response → parse camelCase doc. Map: `id`, `content` ← upstream `content` field; `user_id` ← `metadata.user_id` (NOT top-level `userId` — that's provider-assigned); `created_at` ← `createdAt`; `status` ← upstream `status`. | 404 across budget → `ProviderError(code="ingestion_timeout", provider="supermemory")`; other same as add |
| `list(user_id, limit, cursor)` | `POST /memories/list`        | `{"limit": limit, "page": N, "filters": {"user_id": user_id}}` (N decoded from cursor) | Parse `{"memories":[doc...], "pagination":{"currentPage", "limit", "totalPages", ...}}`. Each doc → `Memory` via the camelCase mapping above. `next_cursor` opaquely encodes `currentPage+1` when `currentPage < totalPages`; else `None`. | Same as add |
| `search(query, user_id, limit, **)` | `POST /search`           | `{"q": query, "limit": limit, "filters": {"user_id": user_id}}`                          | Parse `{"results":[{chunks:[{content, score, ...}], documentId, score, title, ...}], "timing", "total"}`. One `SearchResult` per `documentId`; `score = best chunk score`; `memory.content = title or chunks[0].content`; `memory.id = documentId`. | Same as add |
| `update(id, content)` | not advertised                        | n/a                                                                                      | `capabilities().verbs` excludes `update`; calling the method raises `UnsupportedCapabilityError` BEFORE any HTTP call (FR-009 / FR-111). | n/a |
| `delete(id)`     | `DELETE /memories/{id}`                    | n/a                                                                                      | `204` → `None`; `404` → `None` (idempotent).                                                                                                                       | Same as add |
| `capabilities()` | static                                     | n/a                                                                                      | `Capabilities(verbs=["add","get","list","search","delete","capabilities"], features={"status_field": True, "async_ingestion": True})`. NO `update`, NO `context`, NO `audit`. | n/a |

Key invariants:

- **`Memory.user_id` is ALWAYS read from `metadata.user_id`**, never from
  the top-level `userId` (which is supermemory-assigned and opaque to OMP).
- **camelCase boundary is internal**: every camelCase upstream field is
  remapped at the adapter boundary; OMP `Memory` stays snake_case.
- **`base_url` default is `/v3`**: callers MUST NOT need to override it
  for the public hosted service.
- **`add()` content is preserved literally**: supermemory does not LLM-rewrite,
  so `x-supermemory.original_content` is NOT emitted (saves bytes).
