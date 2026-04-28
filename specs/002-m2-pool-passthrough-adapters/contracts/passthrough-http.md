# Contract — `PassthroughAdapter` ↔ OMP HTTP

Authoritative source: [`spec/omp-0.1.openapi.yaml`](../../../spec/omp-0.1.openapi.yaml).
This table is mechanical; if the OpenAPI changes, this table changes — never the reverse (Principle I).

## Verb → HTTP mapping

| Verb | HTTP method | URL template | Request body | Response body (2xx) | OperationId |
|---|---|---|---|---|---|
| `capabilities()` | GET | `{base}/capabilities` | — | `Capabilities` | `getCapabilities` |
| `add(MemoryInput)` | POST | `{base}/memories` | `MemoryInput` JSON | `Memory` | `addMemory` |
| `list(...)` | GET | `{base}/memories?user_id=…&scope=…&tag=…&since=…&until=…&limit=…&cursor=…` | — | `MemoryPage` | `listMemories` |
| `get(id)` | GET | `{base}/memories/{id}` | — | `Memory` | `getMemory` |
| `update(id, MemoryUpdate)` | PATCH | `{base}/memories/{id}` | `MemoryUpdate` JSON | `Memory` | `updateMemory` |
| `delete(id)` | DELETE | `{base}/memories/{id}` | — | `204 No Content` | `deleteMemory` |
| `search(query, …)` | POST | `{base}/memories/search` | `{query, user_id, scope?, limit?, min_score?}` JSON | `list[SearchResult]` | `searchMemories` |
| `context(query, …)` | POST | `{base}/context` | `{query, user_id, scope?, token_budget?}` JSON | `ContextBlock` | `getContext` |
| `audit(user_id, …)` | GET | `{base}/audit?user_id=…&app=…&since=…&limit=…` | — | `list[AuditEntry]` | `getAudit` |

## Headers

| Header | Value | When |
|---|---|---|
| `Authorization` | `Bearer {api_key}` | Always when `api_key` is set (FR-011). Never logged. |
| `Accept` | `application/json` | Always. |
| `Content-Type` | `application/json` | When request has a body. |
| `User-Agent` | `openmem-python/{version}` | Always. |

## Capability gate (pre-flight)

Before any HTTP call:

```text
if verb not in self._capabilities.verbs:
    raise UnsupportedCapabilityError(verb, provider="passthrough")
```

This MUST run BEFORE the network call (FR-009, EC-003 prevents silent capability drift).

`capabilities()` itself bypasses the gate (it IS the probe).

## Error mapping

Order of evaluation per response:

1. **Body is OMP `Error` envelope** (has `code` and `type`):
   - Use `OMPError.from_response_dict(payload, provider="passthrough")`. This already dispatches to the right subclass per the `code` field (`unauthorized` → `UnauthorizedError`, `not_found` → `NotFoundError`, `invalid_request` → `InvalidRequestError`, `rate_limited` → `RateLimitedError`, `unsupported_capability` → `UnsupportedCapabilityError`, `provider_error` → `ProviderError`, `scope_denied` → `ScopeDeniedError`).
2. **HTTP 4xx, no envelope**: `InvalidRequestError(status_code=…, message=response.text[:200])` (FR-010).
3. **HTTP 5xx, no envelope**: `ProviderError(status_code=…, message=response.text[:200])` (FR-010).
4. **`httpx.TimeoutException` / `ConnectError`**: `ProviderError(message=str(exc))`.
5. **3xx**: follow exactly one redirect (EC-004); raise `ProviderError("redirect loop")` if a second 3xx is returned.

## Body serialization rules

- Request models: `model.model_dump(mode="json", exclude_none=True)` to omit defaults and round-trip datetimes as ISO 8601.
- Response models: `Model.model_validate(response.json())`. Unknown response fields are tolerated (`extra="allow"` on `Memory`; Principle III + V).
- `204 No Content` returns `None` for `delete`; any other 2xx with empty body raises `ProviderError("empty response")`.
