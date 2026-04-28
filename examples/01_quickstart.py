"""OMP Quickstart — verbatim port of SPEC §11.

Run with::

    pip install -e ./sdk-python
    docker run --rm -d -p 5432:5432 -e POSTGRES_PASSWORD=postgres \\
        pgvector/pgvector:pg16
    python examples/01_quickstart.py
"""

from __future__ import annotations

import os

from openmem import Memory


def main() -> None:
    url = os.environ.get(
        "PG_URL", "postgresql://postgres:postgres@localhost:5432/postgres"
    )
    mem = Memory(provider="postgres", url=url)

    # Add
    m = mem.add(
        content="User prefers pnpm over npm",
        user_id="kek",
        scope="coding/preferences",
        tags=["tooling", "nodejs"],
    )
    print(f"added: {m.id}")

    # Search
    results = mem.search(
        query="package manager preferences",
        user_id="kek",
        scope="coding/*",
        limit=5,
    )
    for r in results:
        print(f"  {r.score:.3f}  {r.memory.content}")

    # Get prompt-ready context
    ctx = mem.context(
        query="set up a new node project",
        user_id="kek",
        token_budget=500,
    )
    print(f"\ncontext ({ctx.token_count} tok):\n{ctx.text}")

    # Update / supersede
    updated = mem.update(
        m.id, content="User prefers bun for new projects", supersedes=[m.id]
    )
    print(f"\nsuperseded: {updated.id} supersedes={updated.supersedes}")

    # Forget
    mem.delete(updated.id)
    print("deleted.")

    # Inspect provider capabilities
    caps = mem.capabilities()
    print(f"\nprovider={caps.provider} verbs={caps.verbs}")
    if caps.features.graph_queries:
        print("graph queries supported")


if __name__ == "__main__":
    main()
