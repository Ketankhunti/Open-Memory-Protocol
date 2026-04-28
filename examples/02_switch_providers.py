"""Substitutability demo (SPEC §14 Example A).

Same `run(mem)` function called against two configurations of the same
provider — proof that swapping backends requires zero code change. In
M2, the second call would target a different `provider=...`.
"""

from __future__ import annotations

import os

from openmem import Memory


def run(mem: Memory, label: str) -> None:
    print(f"\n--- {label} ---")
    mem.add(content="user prefers pnpm over npm", user_id="demo")
    mem.add(content="user dislikes verbose CLI output", user_id="demo")
    for r in mem.search("package manager", "demo", limit=3):
        print(f"  {r.score:.3f}  {r.memory.content}")


def main() -> None:
    url = os.environ.get(
        "PG_URL", "postgresql://postgres:postgres@localhost:5432/postgres"
    )
    a = Memory(provider="postgres", url=url)
    b = Memory(provider="postgres", url=url)
    run(a, "instance A")
    run(b, "instance B")


if __name__ == "__main__":
    main()
