# Contract — `LettaAdapter` ↔ Letta Python SDK

Underlying SDK: `letta-client` (PyPI). Imported lazily; `pip install openmem[letta]` installs it.

Letta's primitive is the *agent* with archival memory blocks. We map one OMP `user_id` → one Letta agent (created on first use, cached by user_id).

## Verb mapping

| OMP verb | Letta call | Notes |
|---|---|---|
| `add(MemoryInput)` | `client.agents.passages.create(agent_id=_agent_for(input.user_id), text=input.content)` | Returns `Passage`. |
| `get(id)` | `client.agents.passages.retrieve(agent_id=_resolve_agent(id), passage_id=id)` | Letta passage IDs are agent-scoped; we encode `agent_id` into the OMP id (`mem_<agent>_<passage>`). |
| `update(id, …)` | — | Not supported by Letta passages; not advertised. |
| `delete(id)` | `client.agents.passages.delete(agent_id=…, passage_id=…)` | |
| `list(user_id, …)` | `client.agents.passages.list(agent_id=_agent_for(user_id), limit=limit, after=cursor)` | Cursor-native. |
| `search(query, …)` | `client.agents.passages.search(agent_id=_agent_for(user_id), query=query, limit=limit)` | Vector + temporal supported. |
| `context(query, …)` | composed: `search → format → ContextBlock` | No direct endpoint. |
| `audit(...)` | — | Not advertised. |

## Field translation

| OMP field | Letta field | Mapping |
|---|---|---|
| `id` | `mem_{agent_id}_{passage_id}` | Adapter encodes agent + passage so `get(id)` is stateless. |
| `content` | `text` | passthrough |
| `user_id` | (mapped to `agent_id` via `_agent_for`) | One Letta agent per OMP user_id; agent name = `omp_{user_id}`. |
| `scope` | `metadata.scope` | string passthrough; `capabilities.scopes = "native"` (Letta passes metadata through verbatim) |
| `tags` | `metadata.tags` | list passthrough |
| `created_at` | `created_at` | passthrough |
| `x-letta` | (full Letta record) | Round-trip stash |

## Capabilities payload

```python
Capabilities(
    provider="letta",
    omp_version="0.1",
    verbs=["add", "get", "delete", "list", "search", "context", "capabilities"],
    vector_search=True,
    keyword_search=False,  # Letta search is vector-only
    temporal=True,
    scopes="native",
    supports_supersession=False,
    supports_audit=False,
    max_content_length=10000,
)
```

## Error map

| Letta exception | OMP exception |
|---|---|
| `letta.errors.UnauthorizedError` | `UnauthorizedError` |
| `letta.errors.NotFoundError` | `NotFoundError` |
| `letta.errors.BadRequestError` | `InvalidRequestError` |
| `letta.errors.RateLimitError` | `RateLimitedError` |
| `letta.errors.LettaError` (other) | `ProviderError` |
| `httpx.HTTPError` | `ProviderError` |
