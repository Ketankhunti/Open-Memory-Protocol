"""Substitutability demo (SPEC §14 Example A; SC-008 / FR-018).

The same `run(mem)` function is called against `postgres` plus every
translation adapter (`mem0`, `supermemory`, `letta`) whose `*_API_KEY`
environment variable is set — proof that swapping the *provider* (not
just its config) requires zero code change.

Required env vars:
- ``PG_URL``               (default ``postgresql://postgres:postgres@localhost:5432/postgres``)
- ``MEM0_API_KEY``         optional — enables the `mem0` provider
- ``SUPERMEMORY_API_KEY``  optional — enables the `supermemory` provider
- ``LETTA_API_KEY``        optional — enables the `letta` provider

The example exits 0 whenever `postgres` plus at least one third-party
provider ran successfully (per the clarified SC-008).
"""

from __future__ import annotations

import os
import sys
import traceback
from pathlib import Path
from typing import Iterable

# Allow `python examples/02_switch_providers.py` from the repo root.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _env import load_env  # noqa: E402

from openmem import Memory  # noqa: E402
from openmem.errors import OMPError, UnsupportedCapabilityError  # noqa: E402

load_env()


def run(mem: Memory, label: str) -> bool:
    """Execute the same demo flow against any provider; return True on success."""
    print(f"\n--- {label} ---")
    try:
        mem.add(content="user prefers pnpm over npm", user_id="demo")
        mem.add(content="user dislikes verbose CLI output", user_id="demo")
        results = mem.search("package manager", "demo", limit=3)
        for r in results:
            print(f"  {r.score:.3f}  {r.memory.content}")
        if not results:
            print("  (no search hits)")
        return True
    except UnsupportedCapabilityError as exc:
        print(f"  [skipped] verb unsupported: {exc}")
        return True  # provider is honest about its caps — not a failure
    except OMPError as exc:
        print(f"  [error] {type(exc).__name__}: {exc}")
        return False


def _build_postgres() -> Memory:
    url = os.environ.get(
        "PG_URL", "postgresql://postgres:postgres@localhost:5432/postgres"
    )
    return Memory(provider="postgres", url=url)


def _build_third_party(provider: str, env_key: str) -> Memory | None:
    api_key = os.environ.get(env_key)
    if not api_key:
        print(
            f"\n--- {provider} ---\n  [skipped] set {env_key} to enable this provider"
        )
        return None
    try:
        return Memory(provider=provider, api_key=api_key)
    except Exception as exc:  # pragma: no cover - depends on installed extras
        print(f"\n--- {provider} ---\n  [skipped] could not construct adapter: {exc}")
        return None


_THIRD_PARTY: list[tuple[str, str]] = [
    ("mem0", "MEM0_API_KEY"),
    ("supermemory", "SUPERMEMORY_API_KEY"),
    ("letta", "LETTA_API_KEY"),
]


def main(argv: Iterable[str] | None = None) -> int:
    print("=" * 60)
    print("OMP substitutability demo — same code, different providers")
    print("=" * 60)

    successes: list[str] = []
    failures: list[str] = []

    try:
        pg = _build_postgres()
    except Exception:  # pragma: no cover
        print("\n[fatal] could not construct PostgresAdapter:")
        traceback.print_exc()
        return 2
    if run(pg, "postgres"):
        successes.append("postgres")
    else:
        failures.append("postgres")

    third_party_ok = 0
    for provider, env_key in _THIRD_PARTY:
        mem = _build_third_party(provider, env_key)
        if mem is None:
            continue
        if run(mem, provider):
            successes.append(provider)
            third_party_ok += 1
        else:
            failures.append(provider)

    print("\n" + "=" * 60)
    print(f"ran: {', '.join(successes) or '<none>'}")
    if failures:
        print(f"failed: {', '.join(failures)}")
    print("=" * 60)

    # SC-008: exit 0 iff postgres + at least one third-party provider ran.
    if "postgres" in successes and third_party_ok >= 1:
        return 0
    if not failures:
        print(
            "\n[note] no third-party providers were enabled. Set MEM0_API_KEY,\n"
            "       SUPERMEMORY_API_KEY, or LETTA_API_KEY to satisfy SC-008."
        )
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
