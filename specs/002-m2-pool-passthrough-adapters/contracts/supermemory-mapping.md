# Contract — `SupermemoryAdapter` ↔ Supermemory REST API

Wire choice: direct REST via shared `httpx.Client` (no official Python SDK at M2 cut). `pip install openmem[supermemory]` is a no-op marker extra so the adapter's import is gated by user intent.

Base URL: `https://api.supermemory.ai/v1` (override via `base_url` kwarg).

## Verb mapping

| OMP verb | Method + path | Body / query | Notes |
|---|---|---|---|
| `add(MemoryInput)` | `POST /memories` | `{content, user_id, metadata: {scope, tags}, ...}` | Returns `{id, ...}`. |
| `get(id)` | `GET /memories/{id}` | — | |
| `update(id, …)` | — | — | **Not supported by Supermemory**; not advertised. |
| `delete(id)` | `DELETE /memories/{id}` | — | |
| `list(user_id, …)` | `GET /memories?user_id=…&limit=…&cursor=…` | — | Supermemory is cursor-native; passthrough. |
| `search(query, …)` | `POST /memories/search` | `{query, user_id, limit, threshold}` | `min_score` → `threshold`. |
| `context(query, …)` | composed: `search → format → ContextBlock` | — | No native endpoint. |
| `audit(...)` | — | — | Not advertised. |

## Field translation

| OMP field | Supermemory field | Mapping |
|---|---|---|
| `id` | `id` | passthrough |
| `content` | `content` | passthrough |
| `user_id` | `user_id` | passthrough |
| `scope` | `metadata.scope` | string passthrough; `capabilities.scopes = "tags"` |
| `tags` | `metadata.tags` | list passthrough |
| `source` | `metadata.source` | dict passthrough |
| `embedding_model` | — | omitted (provider-managed; EC-007) |
| `valid_from / valid_to / supersedes` | — | omitted (not modeled by Supermemory) |
| `created_at` | `created_at` | passthrough |
| `x-supermemory` | (full record) | Stashed for round-trip |

## Capabilities payload

```python
Capabilities(
    provider="supermemory",
    omp_version="0.1",
    verbs=["add", "get", "delete", "list", "search", "context", "capabilities"],
    vector_search=True,
    keyword_search=True,
    temporal=False,
    scopes="tags",
    supports_supersession=False,
    supports_audit=False,
    max_content_length=10000,
)
```

## Error map

| Status / signal | OMP exception |
|---|---|
| `401` | `UnauthorizedError` |
| `403` | `ScopeDeniedError` |
| `404` | `NotFoundError` |
| `400 / 422` | `InvalidRequestError` |
| `429` | `RateLimitedError` |
| `5xx` | `ProviderError` |
| `httpx.TimeoutException / ConnectError` | `ProviderError` |
