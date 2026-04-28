# Contract — `Mem0Adapter` ↔ Mem0 Python SDK

Underlying SDK: `mem0ai` (PyPI). Imported lazily inside the adapter; `pip install openmem[mem0]` installs it.

## Verb mapping

| OMP verb | Mem0 SDK call | Notes |
|---|---|---|
| `add(MemoryInput)` | `client.add(messages=[{"role":"user","content":input.content}], user_id=input.user_id, metadata={"scope": input.scope, "tags": input.tags, **x_ext})` | Mem0 stores `metadata` as a free-form dict; `scope` and `tags` live there. |
| `get(id)` | `client.get(memory_id=id)` | Returns Mem0 record dict. |
| `update(id, MemoryUpdate)` | `client.update(memory_id=id, data=update.content)` | Mem0 supports content-only update. Updating tags/scope is mapped to a metadata PATCH via a second call. |
| `delete(id)` | `client.delete(memory_id=id)` | Returns `None`. |
| `list(user_id, …)` | `client.get_all(user_id=user_id, limit=limit, page=cursor_to_page(cursor))` | Mem0 uses page numbers; we wrap them in opaque base64 cursor (EC-005). |
| `search(query, user_id, …)` | `client.search(query=query, user_id=user_id, limit=limit)` | Returns list of `{memory, score}`. |
| `context(query, user_id, …)` | composed: `search(query) → format → ContextBlock` | Mem0 has no native context endpoint. |
| `audit(...)` | — | Not advertised; raises `UnsupportedCapabilityError`. |

## Field translation

| OMP field | Mem0 field | Mapping |
|---|---|---|
| `id` | `id` | passthrough |
| `content` | `memory` | passthrough |
| `user_id` | `user_id` | passthrough |
| `scope` | `metadata["scope"]` | string passthrough; `capabilities.scopes = "tags"` |
| `tags` | `metadata["tags"]` | list passthrough |
| `source` | `metadata["source"]` | dict passthrough |
| `confidence` | `metadata["confidence"]` | passthrough |
| `valid_from`, `valid_to`, `supersedes` | `metadata["valid_from" / "valid_to" / "supersedes"]` | ISO 8601 / list of ids |
| `embedding_model` | — | omitted (Mem0 manages embeddings; EC-007) |
| `created_at` | `created_at` | passthrough |
| `x-mem0` | (full Mem0 record) | Stashed under `extensions["x-mem0"]` for round-trip |

## Capabilities payload

```python
Capabilities(
    provider="mem0",
    omp_version="0.1",
    verbs=["add", "get", "update", "delete", "list", "search", "context", "capabilities"],
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

| Mem0 exception | OMP exception |
|---|---|
| `mem0.exceptions.AuthenticationError` | `UnauthorizedError` |
| `mem0.exceptions.NotFoundError` | `NotFoundError` |
| `mem0.exceptions.ValidationError` | `InvalidRequestError` |
| `mem0.exceptions.RateLimitError` | `RateLimitedError` |
| `httpx.HTTPError` (any other) | `ProviderError` |
| Anything else | `ProviderError(provider="mem0", message=str(exc))` |
