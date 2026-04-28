<!--
SYNC IMPACT REPORT
==================
Version change: 1.0.1 → 1.0.2   [PATCH: align suite path with split test files]
Modified principles: II (path glob — no semantic change)
Fixes: /speckit.analyze finding I4 — the suite is split into
`test_contract_lifecycle.py / _search.py / _errors.py / _compat.py`
per analyze finding D1; the constitution now matches the glob.
----- prior amendments -----
Version change: 1.0.0 → 1.0.1   [PATCH: clarify conformance suite path]
Modified principles: II (path correction only — no semantic change)
Fixes: /speckit.analyze finding I1 — constitution said `tests/test_contract.py`
but SPEC §13 + plan + tasks all use `sdk-python/tests/test_contract.py`.
----- prior ratification -----
Version change: (none) → 1.0.0   [initial ratification]
Modified principles: N/A (first version)
Added sections:
  - Core Principles (I–V)
  - Protocol & Compatibility Constraints
  - Development Workflow & Quality Gates
  - Governance
Removed sections: none
Templates requiring updates:
  - .specify/templates/plan-template.md             ⚠ pending  (Constitution Check
    section is generic; should reference Principles I–V by name in next plan run)
  - .specify/templates/spec-template.md             ✅ no change required (no
    constitution-specific clauses present)
  - .specify/templates/tasks-template.md            ✅ no change required (task
    categories already cover contract/integration/unit testing)
  - .specify/templates/checklist-template.md        ✅ no change required
  - .github/prompts/speckit.*.prompt.md             ✅ no change required (generic)
  - README.md                                       ⚠ pending  (no README at repo
    root yet; create when project scaffolding lands)
Follow-up TODOs: none — all placeholders resolved.
-->

# Open Memory Protocol (OMP) Constitution

## Core Principles

### I. Spec-First, Single Source of Truth (NON-NEGOTIABLE)

The OpenAPI document at `spec/omp-0.1.openapi.yaml` is the canonical definition
of the protocol. SDKs, adapters, conformance tests, documentation, and provider
implementations MUST be derived from or validated against this file. Any change
to verbs, schema fields, error codes, or capability flags MUST land in the spec
first; downstream artifacts follow. No "spec drift" is acceptable: code that
diverges from the spec is a bug in the code, not the spec.

**Rationale:** OMP only has value as a *standard*. Multiple independent
implementations must interoperate, which is impossible without one machine-
readable, version-controlled source of truth.

### II. Adapter Conformance via Shared Contract Tests (NON-NEGOTIABLE)

Every adapter — translation or passthrough, first-party or community — MUST
pass the same parametrized contract test suite (`sdk-python/tests/test_contract_*.py`)
covering the full `add → search → get → update → delete → list → context`
lifecycle, the standard error model, and the capabilities response. A
provider's Conformance Tier (Native / Compatible / Community) is assigned by
test results, never by self-declaration. Tests MUST be written or updated
*before* an adapter ships, and MUST fail before the adapter implementation
exists (Red → Green → Refactor).

**Rationale:** Substitutability is the product. Without enforced conformance,
"one API for any provider" collapses into N subtly incompatible APIs.

### III. Backward and Forward Compatibility

OMP follows SemVer with explicit cross-version rules: a vN client MUST
interoperate with a vN-1 server via graceful degradation through
`/capabilities`, and any client MUST ignore unknown fields and unknown verbs
rather than error. Required fields and verbs MUST NOT be removed, renamed, or
have their semantics changed within a major version. Pre-1.0 breaking changes
require ≥ 60 days advance notice in the spec repo, an entry in `CHANGELOG.md`,
and an SDK migration helper.

**Rationale:** A protocol that breaks its consumers is not a standard. App
developers must trust that code written today keeps working as providers and
the spec evolve.

### IV. Provider Neutrality and User Sovereignty

OMP MUST NOT require any specific vendor, hosted service, or proprietary
infrastructure to function. The reference path (Postgres + pgvector adapter)
MUST remain a fully supported, first-class option. User-owned concepts —
`user_id`, hierarchical `scope`, scope-based consent grants, and the audit
log — are mandatory primitives, not optional features. Adapters MUST NOT
introduce friction (extra accounts, telemetry, license keys) that would
prevent a user from switching backends or self-hosting.

**Rationale:** OMP exists to break vendor lock-in. Any decision that
re-introduces lock-in — even subtly — defeats the protocol's purpose.

### V. Open Extensibility via Namespaced Fields

Providers MAY add proprietary data using JSON fields prefixed `x-<provider>`
(e.g. `x-mem0`, `x-supermemory`). Extension fields MUST NOT override the
semantics of any standard field, MUST NOT be required for a compliant client
to use a memory, and MUST be safely ignored by clients that do not recognize
them. New standard fields are added through the spec amendment process
(Governance section), not by promoting a single provider's extension.

**Rationale:** Without an extension mechanism, providers refuse to adopt the
standard because compliance would force them to abandon differentiation. With
one, they can be fully OMP-compliant *and* keep innovating.

## Protocol & Compatibility Constraints

The following constraints apply to every implementation, SDK, and adapter:

- **Required verbs.** A "Native" or "Compatible" adapter MUST implement
  `add`, `search`, `get`, `delete`, `list`, and `capabilities`. `update`,
  `context`, and `audit` are optional but MUST be advertised correctly via
  `/capabilities.verbs`.
- **Required schema fields.** `id`, `content`, `user_id`, and `created_at`
  are required on every `Memory` returned by any adapter. Other standard
  fields are optional but, when present, MUST conform to the spec's types
  and formats (ISO 8601, 0..1 confidence, etc.).
- **Standard error model.** All errors MUST use the `Error` schema with one
  of the enumerated `code` values. Provider-specific failures MUST be
  wrapped as `provider_error` with the original detail in `message`.
- **Capability negotiation.** SDKs MUST probe `/capabilities` once per
  session and route to a passthrough adapter when `omp_version` is present,
  otherwise to a translation adapter. The probe result MAY be cached for
  the session.
- **Auth.** OAuth 2.1 + PKCE for user-facing flows; API keys for
  server-to-server. Scope grammar `<verb>:<scope-path>` is mandatory; scope
  enforcement is the gateway's / adapter's responsibility.

## Development Workflow & Quality Gates

- **Two reference SDKs required.** Python and TypeScript SDKs MUST be kept
  feature-equivalent. A change shipped in only one SDK is incomplete.
- **Three reference adapters at launch.** Mem0, Supermemory, and
  Postgres + pgvector. The Postgres adapter MUST always be runnable with no
  third-party account.
- **Test-first for protocol changes.** Any spec change MUST be accompanied,
  in the same PR, by (a) updated contract tests and (b) updated SDK type
  bindings. Adapters then update to make the suite green.
- **Gated PR checklist.** Every PR MUST verify:
  1. Spec is the source of truth (Principle I).
  2. Contract tests still pass for all adapters (Principle II).
  3. No required field/verb removed within a major version (Principle III).
  4. No new vendor coupling introduced (Principle IV).
  5. Any new provider-specific data lives behind an `x-` prefix (Principle V).
- **Documentation.** Public-facing changes (new verb, new field, new
  capability) MUST update `spec/OMP-0.1.md`, the OpenAPI file, and at least
  one example under `examples/` in the same PR.

## Governance

This constitution supersedes all other process documents in this repository.
When guidance elsewhere conflicts with the constitution, the constitution
wins until formally amended.

**Amendment procedure.**
1. Open a PR modifying `.specify/memory/constitution.md` with the proposed
   change, a rationale, and a Sync Impact Report (see template at the top
   of this file).
2. The PR MUST update any dependent templates (`plan-template.md`,
   `spec-template.md`, `tasks-template.md`) and prompt files that reference
   the changed principle.
3. Approval requires sign-off from the project maintainers listed in
   `MAINTAINERS.md` (or, until that file exists, the repository owner).
4. On merge, bump the constitution version per the policy below and
   announce the change in `CHANGELOG.md`.

**Versioning policy (constitution).**
- **MAJOR** — Removal or backward-incompatible redefinition of a principle
  or governance rule.
- **MINOR** — Addition of a new principle, new mandatory section, or
  materially expanded normative guidance.
- **PATCH** — Wording clarifications, typo fixes, or non-semantic edits.

**Compliance review.**
- Every PR review MUST treat the gated checklist in *Development Workflow
  & Quality Gates* as blocking.
- Complexity that appears to violate a principle MUST be justified in the
  plan's Complexity Tracking table; unjustified violations block merge.
- A quarterly review MUST audit the latest releases for drift from the
  constitution; findings file issues or amendment PRs as appropriate.

**Runtime guidance.** For day-to-day implementation guidance derived from
these principles (coding patterns, repo layout, testing conventions), see
`README.md` and the agent guidance file at `.github/copilot-instructions.md`.

**Version**: 1.0.2 | **Ratified**: 2026-04-28 | **Last Amended**: 2026-04-28
