"""Cost confirmation helper (T023).

Single point of policy for: "should we ask the user before incurring this
estimated USD cost?". Wired into ``cli.main`` immediately before any
adapter is instantiated so refusal exits *before* any network call.
"""

from __future__ import annotations

import sys
from typing import Callable, Optional


def confirm_or_exit(
    estimated_cost_usd: float,
    *,
    threshold: float,
    yes: bool,
    isatty: bool,
    prompt: Optional[Callable[[str], str]] = None,
) -> None:
    """Return silently when the run may proceed; raise SystemExit(3) otherwise.

    Rules:
    * cost ≤ threshold → proceed
    * ``yes`` flag set → proceed
    * cost > threshold and TTY → ask once; non-y/Y → exit 3
    * cost > threshold and no TTY → exit 3 immediately (cannot prompt safely)
    """
    if estimated_cost_usd <= threshold or yes:
        return
    if not isatty:
        sys.stderr.write(
            f"openmem-eval: estimated ${estimated_cost_usd:.4f} exceeds threshold "
            f"${threshold:.2f}; pass --yes to proceed non-interactively\n"
        )
        raise SystemExit(3)
    ask = prompt or input
    msg = (
        f"Estimated cost ${estimated_cost_usd:.4f} exceeds threshold "
        f"${threshold:.2f}. Proceed? [y/N]: "
    )
    try:
        ans = ask(msg).strip().lower()
    except EOFError:
        ans = ""
    if ans not in {"y", "yes"}:
        sys.stderr.write("openmem-eval: aborted by user\n")
        raise SystemExit(3)


__all__ = ["confirm_or_exit"]
