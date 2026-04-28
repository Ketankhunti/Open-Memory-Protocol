# Quickstart — M2 capabilities

End-user-facing. Each section is independently runnable.

## 1. Pooled Postgres adapter (US1)

```bash
pip install openmem
docker compose up -d postgres   # see docker-compose.yml at repo root
export PG_URL="postgresql://postgres:postgres@localhost:5432/postgres"
```

```python
from openmem import Memory

mem = Memory(
    provider="postgres",
    url=PG_URL,
    pool_min_size=2,
    pool_max_size=20,   # default 10
)

# Now safe to call from many threads concurrently:
import concurrent.futures as cf
with cf.ThreadPoolExecutor(max_workers=20) as ex:
    futures = [ex.submit(mem.add, content=f"item {i}", user_id="u1") for i in range(200)]
    for f in cf.as_completed(futures):
        f.result()
```

No `RLock`, no serialization — actual concurrent DB I/O.

## 2. Native passthrough (US2)

Point the SDK at any OMP-conformant HTTP endpoint:

```python
from openmem import Memory

mem = Memory(base_url="https://memory.example.com", api_key="…")

m = mem.add(content="user prefers pnpm", user_id="u1")
results = mem.search("package manager", user_id="u1")
```

Every verb in [contracts/passthrough-http.md](contracts/passthrough-http.md) works. Verbs the remote does not advertise raise `UnsupportedCapabilityError` *before* hitting the network.

## 3. Translation adapters (US3)

### Mem0

```bash
pip install "openmem[mem0]"
export MEM0_API_KEY="…"
```

```python
mem = Memory(provider="mem0")
mem.add(content="user prefers pnpm", user_id="u1")
```

### Supermemory

```bash
pip install "openmem[supermemory]"
export SUPERMEMORY_API_KEY="…"
```

```python
mem = Memory(provider="supermemory")
mem.add(content="user prefers pnpm", user_id="u1")
```

### Letta

```bash
pip install "openmem[letta]"
export LETTA_API_KEY="…"
```

```python
mem = Memory(provider="letta")
mem.add(content="user prefers pnpm", user_id="u1")
```

## 4. Run the conformance suite for any adapter

```bash
cd sdk-python
pip install -e ".[dev,mem0,supermemory,letta]"
pytest tests -q                       # mock mode (default; offline)
MEM0_API_KEY=… pytest tests -q -k mem0 # live mode for one adapter
```

## 5. Add a new adapter (Constitution Principle II)

Two files — that's it:

1. `sdk-python/openmem/adapters/<your_provider>.py` — subclass `BaseAdapter`, implement verbs, return honest `capabilities()`.
2. `sdk-python/tests/conftest.py` — add a fixture for it and append the name to `adapter`'s `params` list.
3. `sdk-python/openmem/memory.py` — register it in `_resolve_adapter`.

Run `pytest tests -q` — the existing `test_contract_*.py` files run unchanged against your adapter and report a Conformance Tier from the result.

## 6. Switch providers in one line (SC-008)

```bash
python examples/02_switch_providers.py
```

Same `run(mem)` body executed against `provider="postgres"` and `provider="mem0"` (or `passthrough` against the in-process shim if `MEM0_API_KEY` is unset). Outputs print side-by-side and prove substitutability.
