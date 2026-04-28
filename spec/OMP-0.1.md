# Open Memory Protocol (OMP) — Product & Technical Spec v0.1

## 1. Vision

> **One API for any AI memory provider.**
> App developers write against a single interface. Users (or apps) choose which memory backend powers it — Mem0, Supermemory, Zep, Notion, Postgres, or a self-hosted vault — without changing a line of application code.

OMP is to AI memory what **S3 API** is to object storage, **OAuth** is to identity, and **MCP** is to LLM tools.

---

## 2. Problem

Today, every memory provider (Mem0, Supermemory, Zep, Letta, Cognee, …) has:
- A different SDK
- A different schema
- A different concept of users / sessions / scopes
- A different auth model

App developers must:
- Pick **one** provider → lock users in
- OR build N integrations → most don't bother
- OR skip memory entirely

Users cannot:
- Bring their own memory across apps
- Switch providers without losing data
- Control what each app reads/writes

---

## 3. Solution

A **two-part product**:

1. **OMP Specification** — an open, versioned protocol defining standard verbs, schema, auth, and capability negotiation for AI memory.
2. **OMP SDK + Adapters** — reference Python and TypeScript libraries implementing the spec, plus adapters for popular backends.

### Architecture

```
┌──────────────────────────────────────────────────────────────┐
│                     APP DEVELOPERS                           │
│            ChatGPT • Claude • Cursor • Custom agents         │
│                                                              │
│  Code:  mem.add(...) / mem.search(...) / mem.context(...)    │
└────────────────────────────┬─────────────────────────────────┘
                             │
                             │  ONE API (OMP)
                             ▼
┌──────────────────────────────────────────────────────────────┐
│                  OMP LIBRARY / GATEWAY                       │
│  • Protocol enforcement                                      │
│  • Standard schema normalization                             │
│  • Scope / consent enforcement                               │
│  • Audit log                                                 │
│  • Adapter framework                                         │
└────┬──────────┬──────────┬──────────┬──────────┬─────────────┘
     ▼          ▼          ▼          ▼          ▼
  ┌──────┐ ┌────────┐ ┌──────┐ ┌────────┐ ┌─────────────┐
  │ Mem0 │ │Super-  │ │ Zep  │ │ Notion │ │ User's      │
  │      │ │memory  │ │      │ │        │ │ Postgres    │
  └──────┘ └────────┘ └──────┘ └────────┘ └─────────────┘
```

---

## 3a. Strategy: Wrapper → Protocol → Standard

OMP is built in three phases, each with a distinct role for the SDK and adapters.

### Phase 1 — Wrapper (months 0–6)
- Apps write OMP-style code via the SDK.
- The SDK uses **translation adapters** to convert OMP calls into each provider's native API (Mem0, Supermemory, etc.).
- Providers do nothing. They get free traffic via our adapters.
- Goal: **app adoption.**

### Phase 2 — Protocol Recognition (months 6–18)
- App usage of OMP creates demand.
- We work with providers to implement OMP **natively** in their HTTP API.
- Cooperating providers earn an **OMP Native** badge.
- Goal: **provider adoption.**

### Phase 3 — Standard (months 18+)
- Most major providers expose OMP endpoints natively.
- The SDK auto-detects native support via `/capabilities` and uses **passthrough adapters** (thin HTTP clients) instead of translation.
- Translation adapters remain for legacy / non-cooperative / non-API providers (e.g., Notion).
- Goal: **ecosystem standard.** OMP becomes to memory what S3 API is to object storage.

### Why both modes coexist forever
The SDK must always support **both** translation and passthrough adapters. Some providers will never implement OMP natively (e.g., legacy systems, Notion-as-memory) and that's OK — the SDK transparently bridges them.

> **App developers' code never changes across phases.** The SDK and adapters absorb the transition.

---

## 4. Goals & Non-Goals

### Goals
- Define a minimal, unambiguous protocol covering the 80% memory use case
- Ship Python and TypeScript SDKs with a clean, idiomatic interface
- Ship 3 adapters at launch: Mem0, Supermemory, Postgres+pgvector
- Allow capability negotiation so apps degrade gracefully across backends
- Be storage-agnostic; we never require running our infrastructure

### Non-Goals (v0.1)
- Building our own memory storage product
- E2E encryption (deferred to v0.2)
- Multi-vault federation in a single query (deferred)
- Graph-native query language (rely on backend if supported)
- A consumer-facing GUI (SDK + CLI only at launch)

---

## 5. Core Concepts

| Concept | Meaning |
|---|---|
| **Memory** | An atomic unit of remembered information (a fact, preference, event, document chunk) |
| **Provider** | A backend that implements OMP (e.g., Mem0, Supermemory, Postgres adapter) |
| **Scope** | Hierarchical namespace for a memory (e.g., `coding/preferences`, `health/symptoms`) |
| **Consent / Grant** | An app's permission to read/write specific scopes |
| **Capability** | A feature a provider supports (e.g., `vector_search`, `temporal`, `graph_queries`) |
| **Context Block** | A pre-ranked, citation-tagged blob ready for LLM prompt injection |
| **Passthrough Adapter** | Thin SDK adapter used when a provider supports OMP natively |
| **Translation Adapter** | SDK adapter that maps OMP requests to a non-OMP provider's proprietary API |
| **Conformance Tier** | Classification of a provider's OMP support level (Native / Compatible / Community / None) |
| **Extension Field** | An `x-`-prefixed JSON field allowing providers to add proprietary data without breaking compliance |

---

## 6. Standard Memory Schema

```jsonc
{
  "id": "mem_abc123",                     // provider-assigned, unique
  "content": "User prefers pnpm over npm", // string, required
  "user_id": "kek",                        // string, required
  "scope": "coding/preferences",           // string, slash-delimited hierarchy
  "tags": ["tooling", "nodejs"],           // string[]
  "source": {
    "app": "cursor",                       // app that created the memory
    "type": "extracted",                   // extracted | explicit | imported
    "ref": "session_xyz"                   // optional opaque pointer
  },
  "confidence": 0.92,                      // 0..1
  "valid_from": "2026-04-27T10:00:00Z",    // ISO 8601
  "valid_to": null,                        // ISO 8601 or null
  "supersedes": ["mem_old456"],            // ids of memories this replaces
  "embedding_model": "text-embedding-3-small",
  "created_at": "2026-04-27T10:00:00Z",
  "updated_at": "2026-04-27T10:00:00Z"
}
```

Required fields: `id`, `content`, `user_id`, `created_at`.
All others are optional but normalized when present.

---

## 6a. Extension Fields

OMP supports provider-specific extensions via namespaced fields prefixed with `x-`. This allows providers to differentiate without breaking OMP compliance.

### Rules
- Extension fields **must** be prefixed `x-<provider>`.
- Extension fields **must not** override standard field semantics.
- Apps that don't recognize an extension **must** ignore it (forward-compatibility).
- Extensions **must not** be required for an OMP-compliant client to use the memory.

### Example

```jsonc
{
  "id": "mem_abc123",
  "content": "User prefers pnpm over npm",
  "user_id": "kek",
  "scope": "coding/preferences",

  // Standard fields above; provider extensions below
  "x-mem0": {
    "graph_node_id": "g_456",
    "embedding_version": "v3"
  },
  "x-supermemory": {
    "space_id": "sp_789"
  }
}
```

### Why this matters
Without extensions, providers would refuse to adopt OMP because it would force them to give up proprietary features. With extensions, they can be **fully OMP-compliant** while still innovating.

---

## 7. Standard Verbs (HTTP + SDK)

| Verb | HTTP | SDK | Description |
|---|---|---|---|
| Add | `POST /memories` | `mem.add(...)` | Create a memory |
| Search | `GET /memories/search` | `mem.search(...)` | Semantic + keyword search |
| Get | `GET /memories/:id` | `mem.get(id)` | Fetch by id |
| Update | `PATCH /memories/:id` | `mem.update(id, ...)` | Update / supersede |
| Delete | `DELETE /memories/:id` | `mem.delete(id)` | Forget |
| List | `GET /memories` | `mem.list(...)` | Filter by scope/tag/time |
| Context | `POST /context` | `mem.context(query, budget)` | Ranked, prompt-ready block |
| Audit | `GET /audit` | `mem.audit(...)` | Who did what |
| Capabilities | `GET /capabilities` | `mem.capabilities()` | What this provider supports |

---

## 8. Capability Negotiation

Providers declare what they support so apps degrade gracefully.

```jsonc
GET /capabilities
{
  "omp_version": "0.1",            // ← presence signals NATIVE OMP support
  "provider": "mem0",
  "verbs": ["add", "search", "get", "update", "delete", "list", "context"],
  "features": {
    "vector_search": true,
    "keyword_search": true,
    "graph_queries": true,
    "temporal": true,
    "scopes": "native",            // "native" | "tags" | "none"
    "max_content_length": 10000,
    "supports_e2e": false,
    "supports_audit": true,
    "supports_supersession": true
  },
  "limits": {
    "rate_limit_per_minute": 600,
    "max_search_results": 100
  }
}
```

> The presence of `omp_version` indicates the provider speaks OMP natively. The SDK uses this to choose **passthrough vs. translation adapter** (see Section 11a).

---

## 8a. Conformance Tiers

Every provider integrated with OMP is classified into one of four tiers. Tiers are visible in the SDK, docs, and provider directory to create transparency for users and incentive for providers.

| Tier | Badge | Meaning |
|---|---|---|
| **OMP Native** | 🟢 | Provider's own HTTP API implements OMP v0.1+. SDK uses passthrough. |
| **OMP Compatible** | 🟡 | Official adapter exists (built or endorsed by the provider). SDK uses translation. |
| **OMP Community** | 🔵 | Third-party adapter exists. SDK uses translation. No official support. |
| **Not OMP** | ⚪ | No adapter. Does not work with OMP. |

A provider claiming **OMP Native** must:
- Implement all required verbs (`add`, `search`, `get`, `delete`, `list`, `capabilities`)
- Pass the official conformance test suite
- Return `omp_version` in `/capabilities`
- Use the standard error model (Section 10)

Tiers are upgraded by passing the conformance suite, not by self-declaration.

---

## 9. Auth & Scopes

OMP uses **OAuth 2.1 + PKCE** for user-facing flows and **API keys** for server-to-server.

### Scope grammar

```
<verb>:<scope-path>
```

Examples:
- `read:coding/*`
- `write:coding/preferences`
- `delete:health/*`
- `read:*` (full read)

### Example consent prompt (rendered by SDK or gateway)
```
Cursor is requesting access to your memory:
  ✓ read:coding/*
  ✓ write:coding/*
  ✗ read:health
  ✗ read:finance
Duration: 90 days
[ Allow ]   [ Deny ]   [ Customize ]
```

---

## 10. Error Model

All errors are typed and consistent across providers.

```jsonc
{
  "error": {
    "code": "scope_denied",
    "message": "App lacks scope read:health",
    "type": "auth",         // auth | not_found | invalid | rate_limited | provider_error
    "provider": "mem0",
    "request_id": "req_abc"
  }
}
```

Standard codes (v0.1):
`unauthorized`, `scope_denied`, `not_found`, `invalid_request`, `rate_limited`, `unsupported_capability`, `provider_error`.

---

## 11. SDK Interface (Python)

```python
from openmem import Memory

# Pick any provider
mem = Memory(provider="mem0", api_key="...")
# mem = Memory(provider="supermemory", api_key="...")
# mem = Memory(provider="postgres", url="postgresql://...")

# Add
m = mem.add(
    content="User prefers pnpm over npm",
    user_id="kek",
    scope="coding/preferences",
    tags=["tooling", "nodejs"],
)

# Search
results = mem.search(
    query="package manager preferences",
    user_id="kek",
    scope="coding/*",
    limit=5,
)

# Get prompt-ready context
ctx = mem.context(
    query="set up a new node project",
    user_id="kek",
    token_budget=500,
)
prompt = f"Relevant memory:\n{ctx.text}\n\nUser: ..."

# Update / supersede
mem.update(m.id, content="User prefers bun for new projects", supersedes=[m.id])

# Forget
mem.delete(m.id)

# Inspect provider capabilities
caps = mem.capabilities()
if caps.features.graph_queries:
    ...
```

### SDK Interface (TypeScript)

```ts
import { Memory } from "@openmem/sdk";

const mem = new Memory({ provider: "supermemory", apiKey: "..." });

await mem.add({
  content: "User prefers pnpm over npm",
  userId: "kek",
  scope: "coding/preferences",
});

const results = await mem.search({
  query: "package manager preferences",
  userId: "kek",
  scope: "coding/*",
});
```

---

## 11a. SDK Behavior: Auto-detection of Native vs. Translation Mode

The SDK transparently chooses between passthrough (native) and translation (legacy) adapters, based on the provider's `/capabilities` response.

### Detection logic

```python
def _resolve_adapter(provider: str, config: dict) -> BaseAdapter:
    # Step 1: probe capabilities
    try:
        caps = http_get(f"{config['base_url']}/capabilities")
        if caps.get("omp_version"):
            # Provider speaks OMP natively → use passthrough
            return PassthroughAdapter(provider, config, caps)
    except Exception:
        pass

    # Step 2: fall back to known translation adapter
    if provider in TRANSLATION_ADAPTERS:
        return TRANSLATION_ADAPTERS[provider](config)

    raise UnsupportedProviderError(provider)
```

### Adapter types

| Type | Used when | Behavior |
|---|---|---|
| **Passthrough** | Provider returns `omp_version` in `/capabilities` | Forwards OMP requests directly; minimal logic |
| **Translation** | Provider does not speak OMP natively | Maps OMP schema/verbs to provider's proprietary API and vice versa |

### Implications
- App code is identical regardless of which adapter is active.
- Switching a provider from "Compatible" to "Native" requires zero app changes.
- The SDK **caches** the capability probe per session to avoid latency.

---

## 12. Adapter Framework

Each provider is implemented as a class conforming to a base interface.

```python
# openmem/adapters/base.py
from abc import ABC, abstractmethod
from openmem.types import Memory, SearchResult, Capabilities

class BaseAdapter(ABC):
    @abstractmethod
    def add(self, memory: Memory) -> Memory: ...

    @abstractmethod
    def search(self, query: str, user_id: str, **kwargs) -> list[SearchResult]: ...

    @abstractmethod
    def get(self, id: str) -> Memory: ...

    @abstractmethod
    def update(self, id: str, **kwargs) -> Memory: ...

    @abstractmethod
    def delete(self, id: str) -> None: ...

    @abstractmethod
    def list(self, user_id: str, **filters) -> list[Memory]: ...

    @abstractmethod
    def context(self, query: str, user_id: str, token_budget: int) -> str: ...

    @abstractmethod
    def capabilities(self) -> Capabilities: ...
```

Three reference adapters at launch:

| Adapter | Path | Notes |
|---|---|---|
| Mem0 | `openmem/adapters/mem0.py` | Wraps the official `mem0` Python SDK |
| Supermemory | `openmem/adapters/supermemory.py` | Wraps Supermemory REST API |
| Postgres + pgvector | `openmem/adapters/postgres.py` | Reference "BYO" backend; embeds via OpenAI/local model |

---

## 13. Deliverables

### Repo layout
```
openmem/
├── spec/
│   ├── OMP-0.1.md                # human-readable spec
│   └── omp-0.1.openapi.yaml      # machine-readable
├── sdk-python/
│   ├── openmem/
│   │   ├── __init__.py
│   │   ├── memory.py             # public Memory class
│   │   ├── types.py              # pydantic models
│   │   ├── errors.py
│   │   ├── auth.py
│   │   └── adapters/
│   │       ├── base.py
│   │       ├── passthrough.py    # native OMP HTTP client
│   │       ├── mem0.py
│   │       ├── supermemory.py
│   │       └── postgres.py
│   ├── tests/
│   │   ├── test_contract.py      # runs same suite against every adapter
│   │   └── adapters/...
│   └── pyproject.toml
├── sdk-ts/
│   ├── src/...
│   └── package.json
├── examples/
│   ├── 01_quickstart.py
│   ├── 02_switch_providers.py
│   └── 03_chatbot_demo/
└── README.md
```

### Conformance test suite
A single test file (`test_contract.py`) parametrized over all adapters, asserting the same behavior for `add → search → get → update → delete → list → context`. Any new adapter must pass it to be "OMP Conformant".

---

## 14. Example Use Cases

### Example A — Switch providers with one line change

```python
# Day 1: prototype with Mem0
mem = Memory(provider="mem0", api_key=os.environ["MEM0_KEY"])

# Day 30: customer wants self-hosted → swap to Postgres
mem = Memory(provider="postgres", url=os.environ["PG_URL"])

# Application code below is UNCHANGED
mem.add("user prefers dark mode", user_id="kek", scope="ui/preferences")
```

### Example B — Let the user choose their backend

```python
provider = user_settings.get("memory_backend")  # "mem0" | "supermemory" | "postgres"
mem = Memory(provider=provider, **user_settings.get("memory_config"))
```

### Example C — Capability-aware chatbot

```python
caps = mem.capabilities()
if caps.features.temporal:
    results = mem.search("what did I work on last week?",
                         user_id=uid, since="7d")
else:
    results = mem.search("what did I work on last week?", user_id=uid)
```

### Example D — Context block ready for LLM prompt

```python
ctx = mem.context(
    query=user_message,
    user_id=uid,
    scope="coding/*",
    token_budget=400,
)

response = openai.chat.completions.create(
    model="gpt-4o",
    messages=[
        {"role": "system",
         "content": f"Relevant user memory:\n{ctx.text}\n\nCitations: {ctx.citations}"},
        {"role": "user", "content": user_message},
    ],
)
```

---

## 14a. Adapter Lifecycle & Deprecation Policy

Adapters move through stages as providers mature toward native OMP support.

### Stages

| Stage | Description |
|---|---|
| **Active** | Translation adapter is the only way to reach this provider. Fully maintained. |
| **Coexisting** | Provider has shipped native OMP. Both translation (legacy versions) and passthrough (new versions) supported. SDK auto-selects. |
| **Deprecated** | Translation adapter still works but emits a deprecation warning. Marked for removal. |
| **Retired** | Adapter moved to `openmem-legacy` package. Not installed by default. |

### Deprecation timeline (default)
- **T+0**: Provider ships native OMP support.
- **T+3 months**: Translation adapter marked deprecated; warning logged on use.
- **T+12 months**: Adapter moved to `openmem-legacy`.
- **T+24 months**: Removed from `openmem-legacy` unless community maintains it.

### Exceptions
Providers that **never** plan to implement native OMP (e.g., Notion-as-memory, legacy databases) keep their translation adapter as a permanent first-class adapter.

---

## 15. Milestones

| Milestone | Scope | Target |
|---|---|---|
| **M1** | Spec v0.1 published; Python SDK skeleton; Postgres adapter | Week 2 |
| **M2** | Mem0 + Supermemory adapters; conformance test suite | Week 4 |
| **M3** | TypeScript SDK; quickstart docs | Week 5 |
| **M4** | Demo: chatbot with provider dropdown; HN/Twitter launch | Week 6 |
| **M5** | First 3 external adapters (community); 1 framework integration (LangChain/CrewAI) | Week 10 |

---

## 16. Success Metrics

- **Adoption**: ≥ 5 third-party adapters within 3 months
- **Reach**: ≥ 1 mainstream agent framework ships an `OMPMemory` integration
- **Substitutability**: a reference app can switch backends with zero code change (covered by E2E tests)
- **Stars/forks**: organic GitHub growth as a leading indicator

---

## 16a. Versioning Policy

OMP follows **SemVer** with explicit compatibility guarantees.

| Version bump | Allowed changes | Example |
|---|---|---|
| **Patch** (`0.1.0 → 0.1.1`) | Doc fixes, error message clarifications, additive examples | Typo fix |
| **Minor** (`0.1.0 → 0.2.0`) | New optional fields, new optional verbs, new capabilities | Add `/context` endpoint |
| **Major** (`0.x → 1.0`) | Breaking changes to required fields/verbs, removed endpoints | Rename `user_id` → `subject_id` |

### Compatibility rules
- A v0.2 client **must** be able to talk to a v0.1 server (graceful degradation via `/capabilities`).
- A v0.1 client talking to a v0.2 server **must** ignore unknown fields and unknown verbs.
- Providers declare their max supported version in `/capabilities.omp_version`.

### Pre-1.0 caveat
While OMP is below v1.0, breaking changes between minor versions are *possible* but will be:
- Announced ≥ 60 days in advance via the spec repo
- Accompanied by SDK migration helpers
- Documented in `CHANGELOG.md`

---

## 17. Open Questions (track in issues)

1. Should `context()` support multi-vault federation in v0.1 or defer?
2. Do we need a streaming variant of `search()` for very large result sets?
3. How do we handle providers that don't support `user_id` natively (single-tenant stores)?
4. Standardize embedding model declaration, or treat it as opaque?
5. Governance: who owns the spec long-term? (Foundation? BDFL? RFC process?)
6. Should `x-` extensions be limited to a registered list, or fully open?
7. How do we handle providers that implement only a subset of OMP verbs but claim "Native"?
8. Should the conformance suite be free-to-run, or gated for the official Native badge?

---

## 18. Glossary

- **OMP** — Open Memory Protocol
- **Adapter** — Code that translates OMP verbs to a specific backend's API
- **Provider** — A memory backend (Mem0, Supermemory, Postgres, …)
- **Scope** — Hierarchical namespace for grouping & access control of memories
- **Context Block** — A ranked, citation-tagged string ready for LLM prompt injection
- **Conformance** — Passing the official OMP test suite for an adapter
- **Passthrough Adapter** — A thin SDK adapter used when a provider supports OMP natively; mostly an HTTP client
- **Translation Adapter** — An SDK adapter that maps OMP requests to a non-OMP provider's proprietary API
- **Conformance Tier** — Classification of a provider's OMP support level (Native / Compatible / Community / None)
- **Extension Field** — An `x-`-prefixed JSON field allowing providers to add proprietary data without breaking OMP compliance

---

*End of spec v0.1. Treat this document as the source of truth; raise issues to propose changes.*