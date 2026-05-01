# Quickstart — M2.1 Live-API bridges

**Feature**: `003-m2-1-live` | **Date**: 2026-04-28

This file shows how to install, configure, and run the M2.1 live-mode
adapters end-to-end. Mock-mode (the default) is unchanged from M2.

---

## Install

```powershell
# from repo root
cd sdk-python
pip install -e ".[postgres,mem0,supermemory,letta,dev]"
```

The relevant pin updates (M2.1):

- `mem0ai>=2.0,<3` (was `>=0.1`)
- `letta-client>=1.10` (was unpinned)
- `supermemory` SDK NOT installed — REST via `httpx` (M2 invariant)

---

## Configure live keys

Copy `.env.example` to `.env` (gitignored, M2-shipped scaffolding) and fill
in any subset:

```dotenv
# .env
OMP_LIVE=1
MEM0_API_KEY=...
SUPERMEMORY_API_KEY=...
LETTA_API_KEY=...

# Optional: extend the bounded poll for slow ingestions (default 60, hard cap 600)
OMP_INGEST_TIMEOUT=120
```

**Env-var parsing rules** (security-relevant — see data-model.md §4a):

- All values are `.strip()`-ed before use.
- `OMP_LIVE` activates live mode iff stripped value is **exactly `"1"`**.
  Values like `"true"`, `"yes"`, `"0"`, or whitespace keep mock mode.
- `*_API_KEY` activates the matching provider iff stripped value is
  non-empty. Whitespace-only keys keep mock mode (no half-configured
  state).
- Env-var names are **case-sensitive**: `MEM0_API_KEY`,
  `SUPERMEMORY_API_KEY`, `LETTA_API_KEY`, `OMP_LIVE`, `OMP_INGEST_TIMEOUT`.
- `OMP_INGEST_TIMEOUT` MUST be a positive integer in `(0, 600]`;
  out-of-range values fall back to the default 60 with a warning.
- API-key values are **NEVER** logged in any form (no prefixes,
  lengths, or hashes that could enable credential confirmation).

`conftest.py` and `examples/_env.py` auto-load `.env` via `python-dotenv`
(M2-shipped).

---

## Run the contract suite per provider

Mock mode (unchanged baseline — runs even with no keys):

```powershell
cd sdk-python
pytest --no-cov
# expected: 158 passed / 2 skipped + ~1 new for test_add_then_search_finds_original_content
```

Live mode for one provider (mem0):

```powershell
$env:OMP_LIVE='1'
$env:MEM0_API_KEY='...'
pytest -k mem0 --no-cov
# Adapter switches to live; supermemory + letta stay mock (no keys set).
```

Live mode for everything:

```powershell
$env:OMP_LIVE='1'
$env:MEM0_API_KEY='...'
$env:SUPERMEMORY_API_KEY='...'
$env:LETTA_API_KEY='...'
pytest --no-cov
# Live tests register finalizers that clean up all created memories/agents.
```

Live-only tests (ingestion-timeout, LLM-rewrite roundtrip):

```powershell
pytest -m live --no-cov
# These tests are auto-skipped when OMP_LIVE != "1".
```

---

## Run the demo

```powershell
python examples/02_switch_providers.py
```

Output (with all keys set) — abridged:

```text
== postgres ==
  add → mem_… (status=done)
  search('hello') → 1 result
== mem0 ==
  add → 62623fae-… (status=queued)
  search('hello') → 1 result (status of underlying memory: done after ~25 s)
== supermemory ==
  add → JSPuxDxbavar… (status=queued)
  search('hello') → 1 result
== letta ==
  add → mem_agent-…_passage-… (status=done; x-letta.passage_ids=[…])
  search('hello') → 1 result
```

The demo skips any provider whose key is missing.

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `ProviderError(code="ingestion_timeout")` | Provider took >60 s to ingest | Bump `OMP_INGEST_TIMEOUT` |
| `mem0` add returns immediately, but `get(id)` 404s | Live mem0 ingestion (~25 s) | This is by design — call `get()` after a short delay or rely on the bounded poll |
| `supermemory` 401 | Wrong base URL (still pointing at `/v1`) | M2.1 pins `/v3` as the default; ensure no leftover `SUPERMEMORY_BASE_URL` env override |
| `letta` `unexpected keyword argument 'passage_id'` | Old M2 code path | M2.1 contracts/letta-mapping.md pins the correct kwarg |
| Suite hangs on live mode | Cleanup finalizers running serially against a slow provider | Pre-empt with `pytest -p no:cacheprovider --timeout=120`; cleanup failures are warnings, not errors |
