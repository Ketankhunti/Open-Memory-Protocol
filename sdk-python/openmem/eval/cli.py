"""``openmem-eval`` command-line entrypoint.

Exit codes (per `specs/004-eval-kit/contracts/cli.md`):
* 0 — success
* 1 — no providers usable (all skipped/failed before search)
* 2 — bad invocation (argparse exits 2 itself)
* 3 — cost confirmation refused
* 4 — internal error
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Sequence

from openmem.eval import cost as cost_mod
from openmem.eval.cleanup import cleanup as run_cleanup
from openmem.eval.confirm import confirm_or_exit
from openmem.eval.dataset import load_default, load_path, sample
from openmem.eval.report import write_report
from openmem.eval.runner import run as run_eval
from openmem.eval.types import RunConfig

_DEFAULT_PROVIDERS = ("postgres", "mem0", "supermemory", "letta")


def _version_string() -> str:
    try:
        from importlib.metadata import version
        sdk_ver = version("openmem")
    except Exception:  # pragma: no cover
        sdk_ver = "unknown"
    try:
        from openmem.eval.dataset import load_default as _ld
        ds_hash = _ld().dataset_hash
    except Exception:  # pragma: no cover
        ds_hash = "unknown"
    return f"openmem {sdk_ver}, dataset default-{ds_hash}"


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="openmem-eval",
        description="Live-only benchmark harness for the OMP SDK.",
    )
    p.add_argument(
        "--providers",
        type=str,
        default=",".join(_DEFAULT_PROVIDERS),
        help="comma-separated provider names",
    )
    p.add_argument(
        "--live",
        action="store_true",
        help="execute against real provider endpoints (default: dry-run preview)",
    )
    # Per contracts/cli.md §"Mutual exclusion": store --live as a regular flag
    # and derive `dry_run = not live`. --dry-run is accepted for explicitness
    # but is mutually exclusive with --live.
    p.add_argument(
        "--dry-run",
        action="store_true",
        dest="dry_run",
        help="explicit no-network preview (default behaviour when --live is omitted)",
    )
    p.add_argument(
        "--report",
        type=Path,
        default=Path("eval-report.md"),
        help="output Markdown report path",
    )
    p.add_argument(
        "--trace",
        type=Path,
        default=Path("eval-trace.jsonl"),
        help="output JSONL trace path",
    )
    p.add_argument(
        "--dataset",
        type=Path,
        default=None,
        help="path to a directory with facts.jsonl + queries.jsonl",
    )
    p.add_argument(
        "--sample",
        type=int,
        default=None,
        help="run only the first N queries (deterministic)",
    )
    p.add_argument(
        "--cost-threshold",
        type=float,
        default=1.00,
        help="abort if estimated cost exceeds this USD value (default: 1.00)",
    )
    p.add_argument("--yes", action="store_true", help="skip cost confirmation prompt")
    p.add_argument("--cleanup", action="store_true", help="delete written memories after run")
    p.add_argument("-v", "--verbose", action="store_true", help="verbose output")
    p.add_argument("--version", action="store_true", help="print version and exit")
    return p


def _refuse_in_ci() -> int | None:
    """FR-011 — never run live in CI."""
    if os.environ.get("CI") in {"true", "1"} or os.environ.get("GITHUB_ACTIONS") == "true":
        sys.stderr.write(
            "openmem-eval: refusing to run live in CI (set CI=0 to override locally)\n"
        )
        return 4
    return None


def _confirm(prompt: str) -> bool:
    try:
        ans = input(prompt).strip().lower()
    except EOFError:
        return False
    return ans in {"y", "yes"}


def _print_dry_run_table(providers, dataset, cleanup: bool) -> float:
    """Print the per-provider dry-run cost-estimate table to stdout.

    Returns the total estimated USD across all providers.
    """
    print("DRY RUN — no live API calls made.")
    print()
    print(f"{'provider':<14}{'adds':>8}{'searches':>10}{'deletes':>10}{'est. cost':>14}")
    total = 0.0
    for p in providers:
        est = cost_mod.estimate(
            p,
            add_calls=len(dataset.facts),
            search_calls=len(dataset.queries),
            delete_calls=len(dataset.facts) if cleanup else 0,
        )
        total += est.estimated_usd
        print(
            f"{p:<14}{est.add_calls:>8}{est.search_calls:>10}"
            f"{est.delete_calls:>10}{'$' + format(est.estimated_usd, '.4f'):>14}"
        )
    print(f"{'TOTAL':<42}{'$' + format(total, '.4f'):>14}")
    return total


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.version:
        print(_version_string())
        return 0
    if args.live and args.dry_run:
        sys.stderr.write("openmem-eval: --dry-run and --live are mutually exclusive\n")
        return 2
    providers = tuple(p.strip() for p in args.providers.split(",") if p.strip())
    if not providers:
        sys.stderr.write("openmem-eval: no providers requested\n")
        return 1

    cfg = RunConfig(
        providers=providers,
        live=args.live,
        report_path=args.report,
        trace_path=args.trace,
        sample=args.sample,
        cost_threshold_usd=args.cost_threshold,
        yes=args.yes,
        cleanup=args.cleanup,
        verbose=args.verbose,
    )

    try:
        dataset = load_path(args.dataset) if args.dataset else load_default()
    except (FileNotFoundError, ValueError) as exc:
        sys.stderr.write(f"openmem-eval: dataset error: {exc}\n")
        return 4

    if cfg.sample is not None:
        dataset = sample(dataset, cfg.sample)

    # Cost preview / confirmation when going live.
    if cfg.live:
        if (rc := _refuse_in_ci()) is not None:
            return rc
        estimates = [
            cost_mod.estimate(
                p,
                add_calls=len(dataset.facts),
                search_calls=len(dataset.queries),
                delete_calls=len(dataset.facts) if cfg.cleanup else 0,
            )
            for p in providers
        ]
        total = cost_mod.total_estimated(estimates)
        try:
            confirm_or_exit(
                total,
                threshold=cfg.cost_threshold_usd,
                yes=cfg.yes,
                isatty=sys.stdin.isatty(),
                prompt=lambda msg: "y" if _confirm(msg) else "n",
            )
        except SystemExit as exc:
            return int(exc.code) if exc.code is not None else 3
        if cfg.verbose:
            print(f"Estimated total cost: ${total:.4f}")
    else:
        # Dry-run: print the per-provider cost-estimate table to stdout.
        _print_dry_run_table(providers, dataset, cfg.cleanup)

    try:
        results = run_eval(cfg, dataset)
    except Exception as exc:  # pragma: no cover - safety net
        sys.stderr.write(f"openmem-eval: internal error: {exc}\n")
        return 4

    write_report(cfg.report_path, cfg, results, dataset_hash=dataset.dataset_hash)
    if cfg.verbose:
        print(f"Report written to {cfg.report_path}")

    if cfg.cleanup and cfg.live:
        from openmem.eval.runner import _default_factory

        for p in providers:
            try:
                mem = _default_factory(p)
                deleted = run_cleanup(mem, user_id=cfg.user_id())
                if cfg.verbose:
                    print(f"cleanup[{p}]: deleted {deleted}")
            except Exception as exc:  # pragma: no cover
                sys.stderr.write(f"openmem-eval: cleanup failed for {p}: {exc}\n")

    # Exit 1 only if every provider produced zero usable results.
    if all(not r.query_results and r.status != "skipped" for r in results):
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
