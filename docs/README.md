# OpenMemory Protocol docs site

This folder is the Mintlify-powered documentation site for the **Open Memory Protocol**, deployed at <https://openmem.blog>.

## Local preview

Install the Mintlify CLI once:

```bash
npm i -g mintlify
```

Then from this directory:

```bash
mintlify dev
```

Mintlify reads `mint.json` and serves the site at `http://localhost:3000`.

## Structure

```
docs/
├── mint.json                       # Site config + nav
├── introduction.mdx                # Landing page
├── quickstart.mdx
├── learn/                          # Conceptual guides
├── specification/0.1/              # Versioned normative spec (prose)
├── sdk/                            # Per-language SDK references
├── server.mdx                      # omp-server reference
├── providers.mdx                   # Adapter matrix
├── community/                      # Governance, contributing, RFCs
├── faq.mdx
├── api-reference/                  # Generated from openapi.yaml
│   ├── introduction.mdx
│   └── openapi.yaml                # Mirror of /spec/omp-0.1.openapi.yaml
└── eval/                           # (Pre-existing eval harness docs)
```

## Editing rules

1. **Spec changes** must update both `/spec/omp-0.1.openapi.yaml` (the normative source) **and** the matching prose page under `docs/specification/0.1/` in the same PR.
2. **Versioned spec pages** under `docs/specification/<version>/` are immutable once a version ships. New versions live alongside (e.g., `0.2/`).
3. **Don't break links.** All page slugs are referenced from blog posts, funding decks, and external SDKs.

## Keeping the OpenAPI mirror in sync

`docs/api-reference/openapi.yaml` is a byte-for-byte mirror of the normative
`spec/omp-0.1.openapi.yaml`. Whenever you edit the spec, run:

```powershell
pwsh scripts/sync-docs-openapi.ps1
```

(or just `cp spec/omp-0.1.openapi.yaml docs/api-reference/openapi.yaml` on Unix).

CI enforces this via the `docs-openapi-sync` job — if the mirror drifts, the
build fails with the exact `cp` command to run.

## Deployment

Deploy via Mintlify (GitHub app integration). Pushes to `main` redeploy automatically.
