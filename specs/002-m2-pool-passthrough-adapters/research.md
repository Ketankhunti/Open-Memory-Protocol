# Phase 0 Research — M2

All assumptions from [spec.md](spec.md) `## Assumptions` are evaluated here. Every NEEDS CLARIFICATION is resolved.

## R-001 · Postgres pool implementation

- **Decision**: Use `psycopg_pool.ConnectionPool` (sync) from the `psycopg-pool` package, maintained by the same authors as `psycopg` 3.
- **Rationale**:
  - Drops in beside the existing `psycopg.connect()` call — same connection objects, same cursor API.
  - Built-in `min_size`/`max_size`/`timeout`/`reconnect_failed` covers FR-004 (exhaustion) and FR-005 (broken-conn recycle) with no custom code.
  - Released, stable, widely used in production. Threadsafe by design.
- **Alternatives considered**:
  - **`pgbouncer` external pool** — adds a deployment dep, violates Principle IV (Postgres path must run with no extra service).
  - **Custom `queue.Queue` of connections** — reinventing what `psycopg-pool` already does correctly; rejected on YAGNI.
  - **`asyncpg` + an asyncio event loop** — would force an async-first SDK; the rest of M2 is sync (per spec assumptions). Async is deferred.

## R-002 · HTTP client for `PassthroughAdapter`

- **Decision**: `httpx.Client` (sync), reused across verb calls inside one adapter instance.
- **Rationale**:
  - `httpx` is already a top-level dependency (used today by `PassthroughAdapter._probe`).
  - `httpx.MockTransport` enables in-process testing of the full conformance suite without a real HTTP server (supports SC-003 + SC-006 timing).
  - Connection reuse via persistent `Client` instance avoids TCP handshake overhead per verb call.
- **Alternatives considered**:
  - **`requests`** — adds a second HTTP dep, no MockTransport equivalent, no async upgrade path.
  - **stdlib `http.client`** — too low-level for proper error/redirect/timeout handling.

## R-003 · Translation adapter wire choice (per provider)

| Provider | Choice | Rationale |
|---|---|---|
| Mem0 | `mem0ai` Python SDK (`pip install mem0ai`) | Official, covers all needed verbs (`add`, `search`, `get`, `update`, `delete`, `get_all`); already widely adopted; auto-handles auth and retries. |
| Supermemory | Direct REST via `httpx` (no official Python SDK at GA) | Avoids depending on a third-party wrapper that may lag the API; thin mapper sits well in `_http.py`. |
| Letta | `letta-client` Python SDK (`pip install letta-client`) | Official client, covers archival memory verbs we need; agent-scoped concepts map cleanly to OMP `user_id`. |

- **Alternatives considered**:
  - REST for all three: rejected for Mem0/Letta because their Python SDKs handle pagination/auth in ways the mapper would otherwise have to re-implement.
  - SDK for Supermemory: no stable official one exists at the M2 cut date.

## R-004 · Test isolation strategy

- **Decision**: **Default = mock mode** for all three translation adapters; **opt-in live mode** via env vars.
  - Mock mode: each adapter's HTTP/SDK transport is patched at module boundary; recorded JSON fixtures under `sdk-python/tests/adapters/fixtures/{mem0,supermemory,letta}/*.json` provide deterministic responses.
  - Live mode: setting `MEM0_API_KEY` / `SUPERMEMORY_API_KEY` / `LETTA_API_KEY` switches the matching adapter to a real network call; others stay mocked.
  - CI runs only mock mode (no secret in CI); a separate maintainer-only workflow runs live mode nightly.
- **Rationale**:
  - Reproducible CI (Principle II — test results gate the conformance tier).
  - No third-party account required to run the SDK or its tests (Principle IV).
  - Recorded fixtures are versioned and auditable for spec drift.
- **Alternatives considered**:
  - **VCR.py cassettes** — heavier dep; harder to read/edit by hand; rejected for keeping fixtures human-readable JSON.
  - **Live-only with skip-if-no-credentials** — would mean the conformance suite is effectively skipped in CI for these adapters, defeating Principle II.

## R-005 · Capability shape per adapter (M2 cut)

| Adapter | Verbs | `vector_search` | `keyword_search` | `temporal` | `scopes` | `supports_supersession` | `supports_audit` |
|---|---|---|---|---|---|---|---|
| postgres (unchanged) | add, get, update, delete, list, search, context | true | true | true | native | true | false |
| passthrough | (whatever remote advertises) | (mirror) | (mirror) | (mirror) | (mirror) | (mirror) | (mirror) |
| mem0 | add, get, update, delete, list, search, context | true | true | false | tags | false | false |
| supermemory | add, get, delete, list, search, context | true | true | false | tags | false | false |
| letta | add, get, delete, list, search, context | true | false | true | native | false | false |

- **Rationale**: Verb sets reflect each provider's documented surface as of 2026-04-28. Anything not in the verb list raises `UnsupportedCapabilityError` per FR-009 (no false greens, no false reds — SC-004).
- **Alternatives considered**:
  - Forcing every adapter to fake `update` via `delete + add` under the hood — rejected: hides real behavior, violates Principle I (capabilities lie).

## R-006 · `pytest-timeout` default

- **Decision**: `pytest-timeout >= 2.3` in `[dev]` extras; `pyproject.toml` `[tool.pytest.ini_options]` adds `timeout = 30`. Concurrency stress test overrides via `@pytest.mark.timeout(120)`.
- **Rationale**: 30s catches every realistic hang while comfortably accommodating slowest M1 test (~3s). 120s headroom for the 200-thread × pool-size-10 throughput test (SC-001).

## R-007 · `02_switch_providers.py` second provider

- **Decision**: Pair `provider="postgres"` with `provider="mem0"` (mock mode by default, live with `MEM0_API_KEY`).
- **Rationale**: Mem0 supports the most M1 verbs (matches `postgres` capability set most closely), so the side-by-side `run(mem)` body is the cleanest substitutability demo (SC-008). Falls back to `passthrough` against an in-process shim if `MEM0_API_KEY` not set, so the example always runs offline.
- **Alternatives considered**:
  - `letta`: missing `update` — would force `try/except UnsupportedCapabilityError` in the example, muddling the substitutability message.
  - `supermemory`: similar gap on `update`.
