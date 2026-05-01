# Contract: HTTP server (`omp-server`) (PR-B)

This document specifies the binding contract for the FastAPI passthrough server.
Every clause is enforced by tests in `tests/server/`.
The OpenAPI spec at `spec/omp-0.1.openapi.yaml` is the **canonical** source for paths/schemas/codes — anything below that diverges is a bug in this document.

---

## 1. Routes (mirrors `spec/omp-0.1.openapi.yaml`)

| Method | Path | Handler | Success status | Returns |
|---|---|---|---|---|
| `POST` | `/v1/memories` | `add` | `201 Created` | `Memory` |
| `GET` | `/v1/memories/{id}` | `get` | `200 OK` | `Memory` |
| `PATCH` | `/v1/memories/{id}` | `update` | `200 OK` | `Memory` |
| `DELETE` | `/v1/memories/{id}` | `delete` | `204 No Content` | empty |
| `GET` | `/v1/memories` | `list` | `200 OK` | `MemoryPage` |
| `POST` | `/v1/search` | `search` | `200 OK` | `list[SearchHit]` |
| `POST` | `/v1/context` | `context` | `200 OK` | `ContextBlock` |
| `GET` | `/v1/capabilities` | `capabilities` | `200 OK` | `Capabilities` |
| `GET` | `/healthz` | `health` | `200 OK` / `503` | `{"status":"ok"}` or Error |

Every route handler is `async def` and obtains the `AsyncMemory` via `Depends(get_memory)`.

## 2. `user_id` propagation

| Clause | Requirement |
|---|---|
| C-UID-1 | All verb routes accept `user_id` from the JSON request body (POST/PATCH) or from the `X-User-Id` header (GET/DELETE). |
| C-UID-2 | If `user_id` is missing or empty/whitespace, the server MUST respond `400` with `code = invalid_request` BEFORE calling the adapter. |
| C-UID-3 | `user_id` MUST NEVER appear in any log line (FR-020). |

## 3. Error envelope mapping (FR-016, FR-017)

```json
{ "error": { "code": "<enum>", "message": "<str>", "details": { ... } } }
```

| Exception | HTTP | `code` |
|---|---|---|
| `NotFoundError` | 404 | `not_found` |
| `InvalidRequestError` | 400 | `invalid_request` |
| `UnauthorizedError` | 401 | `unauthorized` |
| `ScopeDeniedError` | 403 | `scope_denied` |
| `RateLimitedError` | 429 | `rate_limited` |
| `UnsupportedCapabilityError` | 405 | `unsupported_capability` |
| `ProviderError(code="ingestion_timeout")` | 504 | `ingestion_timeout` |
| `ProviderError(other)` | 502 | `provider_error` |
| Unhandled `Exception` | 500 | `internal_error` |
| Body too large (FR-021) | 413 | `payload_too_large` |
| Pool exhausted (FR-019 path) | 503 | `provider_unavailable` |

Test (`test_server_errors.py`) parametrizes over every row.

## 4. Request size limit (FR-021)

| Clause | Requirement |
|---|---|
| C-SIZ-1 | Request bodies > `OmpServerConfig.max_request_bytes` (default 1 MiB) MUST return `413` with `code = payload_too_large` BEFORE Pydantic validation runs. |
| C-SIZ-2 | The check MUST inspect `Content-Length`; if absent, the server reads the body in bounded chunks and aborts at the limit. |

## 5. CORS (FR-022)

| Clause | Requirement |
|---|---|
| C-CORS-1 | If `OmpServerConfig.cors_origins` is empty (default), CORS middleware is NOT installed. Every cross-origin request gets the browser's standard CORS rejection. |
| C-CORS-2 | If non-empty, FastAPI's `CORSMiddleware` is installed with `allow_origins=list(cors_origins)`, `allow_credentials=False`, `allow_methods=["GET","POST","PATCH","DELETE"]`, `allow_headers=["Content-Type","X-User-Id","X-Request-Id"]`. |

## 6. Health endpoint (FR-019)

| Clause | Requirement |
|---|---|
| C-HEA-1 | `GET /healthz` is exempt from the `user_id` check. |
| C-HEA-2 | For `postgres`: acquire+release a pool connection within 1 s timeout; success → 200, timeout → 503. |
| C-HEA-3 | For `passthrough`: `HEAD <base_url>` within 2 s; 2xx/3xx → 200, else 503. |
| C-HEA-4 | For `mem0`/`supermemory`/`letta`: returns 200 unconditionally (paid endpoint protection). MUST be documented in `--help` output and the route docstring. |

## 7. Logging (FR-020)

Format: `<iso8601> <level> <method> <path> <status> <latency_ms>ms req=<request_id>`

| Clause | Requirement |
|---|---|
| C-LOG-1 | Every request emits exactly one log line at INFO. |
| C-LOG-2 | Log lines MUST NOT contain: request body, response body, `user_id`, any header beginning with `Authorization`, any field whose key matches `(?i)password|secret|token|key|api_key`. |
| C-LOG-3 | `request_id` is `X-Request-Id` from the request if present (and matches `^[A-Za-z0-9._\-]{1,64}$`), else a fresh UUID4. The same id is set on the response as `X-Request-Id`. |
| C-LOG-4 | A test (`test_server_logging.py`) injects a sensitive payload and asserts no forbidden substring appears in captured log output. |

## 8. OpenAPI conformance (FR-015)

Test `test_server_openapi_conformance.py`:
1. Loads `spec/omp-0.1.openapi.yaml` once per session into a `jsonschema` resolver.
2. For each route × representative success+error case (≈25 cases), issues the request via `httpx.AsyncClient(app=app)`.
3. Validates the response body against the matching `responses[<status>].content["application/json"].schema` from the spec.
4. Asserts `Content-Type: application/json` for every JSON body.
5. Asserts `X-Request-Id` is echoed.

PR-B's coverage gate requires this test to pass at 100% (no skips).

## 9. Cancellation on client disconnect (FR-018)

| Clause | Requirement |
|---|---|
| C-DIS-1 | When the ASGI scope reports `http.disconnect` for an in-flight request, the route task MUST be cancelled. |
| C-DIS-2 | Cancellation MUST propagate through `AsyncMemory.<verb>` and abort backend work per the AsyncMemory cancellation contract. |
| C-DIS-3 | Test simulates client disconnect via `httpx.AsyncClient` cancellation and asserts the postgres pool's `size_used` returns to baseline within 1 s. |

## 10. CLI (`omp-server`)

```
omp-server --provider <name> [--host <h>] [--port <p>]
          [--max-request-bytes <n>] [--cors-origins <csv>]
          [--log-level <lvl>]
          [provider-specific flags...]
```

Provider-specific flags:
- `--url <postgres-url>` (postgres)
- (mem0/supermemory/letta): no flag; reads `*_API_KEY` from env

Every flag has a matching env var (per FR-013). CLI > env > default.

| Clause | Requirement |
|---|---|
| C-CLI-1 | `omp-server --help` text MUST contain the literal phrase "trusted-network deployment only" and "auth deferred" (FR-023). |
| C-CLI-2 | `omp-server --version` prints `omp-server <pkg-version>` and exits 0. |
| C-CLI-3 | Missing required provider config (e.g. postgres without `--url`/`OMP_POSTGRES_URL`) exits 2 with stderr message starting `omp-server: missing config:`. |
| C-CLI-4 | Successful boot prints `omp-server: serving <provider> at http://<host>:<port>` to stderr exactly once. |

## 11. Out of scope (explicit non-goals for v1)

- Authentication / Authorization (no API key check, no bearer token, no mTLS)
- Streaming endpoints (SSE, WebSocket)
- Multi-tenant routing (one provider per server process)
- Metrics endpoint (`/metrics` Prometheus) — deferred
- Async pool warming during startup beyond `await mem.__aenter__()`
- Graceful drain during shutdown (uvicorn defaults are accepted)
