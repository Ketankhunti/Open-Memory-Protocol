# Contract: `AsyncMemory` + `AsyncBaseAdapter` (PR-A)

This document specifies the binding contract for the async facade.
Implementations MUST satisfy every clause; tests in `tests/async/` enforce them.

---

## 1. Verb signatures

For every verb on `Memory`, `AsyncMemory` MUST expose the **same parameter list, defaults, and return type**, prefixed by `async`.

A static check (`test_async_facade.py::test_signatures_match_sync`) MUST inspect both classes and assert equality of:
- positional/keyword parameter names
- default values
- type annotations (`get_type_hints` comparison)
- return type annotation
The async versions MUST additionally be coroutines (`inspect.iscoroutinefunction(...)`).

## 2. Construction & lifecycle

| Clause | Requirement |
|---|---|
| C-LIFE-1 | `AsyncMemory(provider="postgres", url="...")` returns within 5 ms on a laptop (no network/db I/O). |
| C-LIFE-2 | First verb call MAY take longer (lazy pool init); subsequent calls reuse the pool. |
| C-LIFE-3 | `async with AsyncMemory(...) as mem: ...` calls `__aenter__` (eager pool init) and `__aexit__` (close). |
| C-LIFE-4 | After `await mem.close()`, every verb call raises `RuntimeError("AsyncMemory is closed")`. |
| C-LIFE-5 | `await mem.close()` is idempotent (second call is a no-op, no exception). |

## 3. Cancellation contract (FR-008, R3)

| Clause | Adapter tier | Requirement |
|---|---|---|
| C-CAN-1 | Native (postgres, passthrough) | Cancelling a task awaiting any verb MUST cause `asyncio.CancelledError` to propagate to the awaiter within 50 ms. |
| C-CAN-2 | Native | The underlying connection/socket MUST be released to its pool within 500 ms of cancellation. Test asserts via `pool.size()` / `httpx.AsyncClient.is_closed`. |
| C-CAN-3 | Native (postgres only) | The server-side query MUST be aborted: `pg_stat_activity` MUST NOT show the cancelled query 1 second after cancellation. |
| C-CAN-4 | Best-effort (threadwrap) | Awaiter receives `CancelledError` immediately. Worker thread completes the wrapped sync call in the background. The wrapper logs at DEBUG when the orphaned call completes (visibility, not requirement). |
| C-CAN-5 | All tiers | Cancellation MUST NOT corrupt the adapter's pool/state — subsequent verb calls on the same `AsyncMemory` MUST succeed. |

## 4. Cross-loop safety

| Clause | Requirement |
|---|---|
| C-LOOP-1 | If `AsyncMemory` is constructed under loop A and a verb is awaited under loop B (different `id(asyncio.get_running_loop())`), the verb MUST raise `RuntimeError("AsyncMemory is bound to a different event loop")` BEFORE any backend call. |
| C-LOOP-2 | If `AsyncMemory` is constructed outside any loop (no `asyncio.get_running_loop()`), the loop is captured on first verb call instead of construction. |

## 5. Error parity with sync `Memory`

| Clause | Requirement |
|---|---|
| C-ERR-1 | For every condition where `Memory.<verb>` raises `<ExcClass>`, `AsyncMemory.<verb>` MUST raise the SAME `<ExcClass>` with an equivalent (string-equal where deterministic) message. |
| C-ERR-2 | `ProviderError.code` MUST match between sync and async for the same backend failure. |
| C-ERR-3 | `InvalidRequestError` for empty/whitespace `user_id` MUST be raised BEFORE any backend call (defends cross-user leakage). Validated by a test that uses a mock adapter whose methods raise on call. |

## 6. Threadpool wrapper (`AsyncThreadwrapAdapter`)

| Clause | Requirement |
|---|---|
| C-TW-1 | Wraps any object satisfying the existing sync `BaseAdapter` protocol. |
| C-TW-2 | Owns a `concurrent.futures.ThreadPoolExecutor` whose lifetime matches the wrapper. `close()` calls `executor.shutdown(wait=False, cancel_futures=True)`. |
| C-TW-3 | Forwards `wait_for_ingest` to the sync adapter via `run_in_executor`. The default no-op `BaseAdapter.wait_for_ingest` is preserved. |
| C-TW-4 | The wrapper MUST NOT mutate the wrapped sync adapter's state directly. All access goes through the executor. |

## 7. Conformance test inventory (`tests/async/`)

| File | What it tests |
|---|---|
| `test_async_facade.py` | Signature parity (§1), construction (§2), cross-loop safety (§4), close idempotency (C-LIFE-5). |
| `test_async_contract_lifecycle.py` | Parametrized over every adapter: add → get → update → list → delete cycle, return shapes match sync. |
| `test_async_contract_search.py` | Parametrized: `search` and `context` return same hit shape as sync; user_id scoping enforced. |
| `test_async_contract_errors.py` | Parametrized: every error class fires under the same conditions as sync (§5). |
| `test_async_cancellation.py` | Native-tier cancellation (§3, C-CAN-1..3) using the postgres + passthrough adapters. Threadwrap best-effort behavior (C-CAN-4) using a controllable mock adapter. |
| `test_async_threadwrap.py` | Threadwrap-specific: executor isolation per instance, shutdown on close, sync-state non-mutation. |

All tests MUST pass with `pytest-asyncio` auto-mode and contribute to the existing 85% coverage gate.

## 8. Imports & extras

| Clause | Requirement |
|---|---|
| C-EXT-1 | `from openmem import AsyncMemory` works iff `pip install openmem[async]` was run. |
| C-EXT-2 | If the `[async]` extras are missing, the import raises `ImportError` whose message contains the exact string `pip install 'openmem[async]'`. |
| C-EXT-3 | `import openmem` (without using `AsyncMemory`) MUST succeed even when `[async]` extras are absent. |
