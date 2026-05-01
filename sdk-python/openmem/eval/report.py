"""Markdown report writer."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from openmem.eval.types import ProviderResult, RunConfig


def _fmt_money(usd: float) -> str:
    return f"${usd:.4f}"


def _fmt_ms(ms: float) -> str:
    return f"{ms:.1f}"


def write_report(
    path: Path,
    cfg: RunConfig,
    provider_results: Iterable[ProviderResult],
    *,
    dataset_hash: str,
) -> None:
    """Write a Markdown summary to `path`."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    provider_results = list(provider_results)

    mode = "live" if cfg.live else "dry-run"
    lines: list[str] = []
    lines.append("# OMP Eval Report")
    lines.append("")
    lines.append(f"- **Run ID**: `{cfg.run_id}`")
    lines.append(f"- **Mode**: {mode}")
    lines.append(f"- **Dataset hash**: `{dataset_hash}`")
    lines.append(f"- **Generated**: {datetime.now(timezone.utc).isoformat()}")
    lines.append(f"- **Providers**: {', '.join(cfg.providers)}")
    if cfg.sample is not None:
        lines.append(f"- **Sample**: first {cfg.sample} queries (`sample={cfg.sample}`)")
    lines.append("")

    lines.append("## Results")
    lines.append("")
    lines.append(
        "| Provider | Status | recall@1 | recall@5 | MRR | "
        "ingest p50/p95/p99 (ms) | search p50/p95/p99 (ms) | errors | est. cost |"
    )
    lines.append(
        "|---|---|---:|---:|---:|---|---|---:|---:|"
    )
    for pr in provider_results:
        m = pr.metrics
        lines.append(
            "| {prov} | {status} | {r1:.3f} | {r5:.3f} | {mrr:.3f} | "
            "{ip50}/{ip95}/{ip99} | {sp50}/{sp95}/{sp99} | {errs} | {cost} |".format(
                prov=pr.provider,
                status=pr.status,
                r1=m.recall_at_1,
                r5=m.recall_at_5,
                mrr=m.mrr,
                ip50=_fmt_ms(m.ingest_p50_ms),
                ip95=_fmt_ms(m.ingest_p95_ms),
                ip99=_fmt_ms(m.ingest_p99_ms),
                sp50=_fmt_ms(m.search_p50_ms),
                sp95=_fmt_ms(m.search_p95_ms),
                sp99=_fmt_ms(m.search_p99_ms),
                errs=m.error_count,
                cost=_fmt_money(pr.estimated_cost_usd),
            )
        )

    # Notes section for any provider with metrics.note set
    notes = [(pr.provider, pr.metrics.note) for pr in provider_results if pr.metrics.note]
    # Re-iterate provider_results above consumed if it's a generator → coerce
    # Caller passes a list per the contract; if not, the loop above already
    # exhausted it and notes would be empty. Rebuild defensively:
    # (no-op when caller already supplied a list)
    if notes:
        lines.append("")
        lines.append("## Notes")
        lines.append("")
        for prov, note in notes:
            lines.append(f"- **{prov}**: {note}")

    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


__all__ = ["write_report"]
