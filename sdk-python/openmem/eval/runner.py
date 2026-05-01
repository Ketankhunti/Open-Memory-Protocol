"""Runner — orchestrates ingest + search across providers.

Per R11 every fact is dual-stamped:
* content prefix ``[fact_id=f-NNNN] `` (regex-recoverable)
* tag ``fact:f-NNNN`` (fallback when an adapter rewrites content)
"""

from __future__ import annotations

import re
import time
from typing import Any, Callable, Optional, Sequence

from openmem.eval import cost as cost_mod
from openmem.eval.scorer import compute_metrics
from openmem.eval.trace import TraceWriter
from openmem.eval.types import (
    Dataset,
    ErrorRecord,
    Metrics,
    ProviderResult,
    QueryResult,
    RunConfig,
)

_FACT_PREFIX_RE = re.compile(r"^\[fact_id=([^\]]+)\] ")


def _stamp(fact_id: str, content: str) -> str:
    return f"[fact_id={fact_id}] {content}"


def _recover_fact_id(*, content: str | None, tags: Sequence[str] | None) -> str | None:
    if content:
        m = _FACT_PREFIX_RE.match(content)
        if m:
            return m.group(1)
    if tags:
        for t in tags:
            if t.startswith("fact:"):
                return t[5:]
    return None


def _default_factory(provider: str, **kwargs: Any):  # pragma: no cover
    """Default factory: build a real openmem.Memory.

    Resolves provider-specific connection settings from environment
    variables so the eval CLI works against the standard local postgres
    container (`OMP_POSTGRES_URL`) and provider-key envs.
    """
    import os
    from openmem.memory import Memory

    if provider == "postgres":
        if "embedder" not in kwargs:
            from openmem.adapters.embedder import FakeEmbedder
            kwargs["embedder"] = FakeEmbedder()
        url = os.environ.get("OMP_POSTGRES_URL") or os.environ.get("PG_URL")
        if url:
            kwargs.setdefault("url", url)
    elif provider == "mem0":
        key = os.environ.get("MEM0_API_KEY")
        if key:
            kwargs.setdefault("api_key", key)
    elif provider == "supermemory":
        key = os.environ.get("SUPERMEMORY_API_KEY")
        if key:
            kwargs.setdefault("api_key", key)
    elif provider == "letta":
        key = os.environ.get("LETTA_API_KEY")
        if key:
            kwargs.setdefault("api_key", key)
    return Memory(provider=provider, **kwargs)


def _build_dry_run(cfg: RunConfig, dataset: Dataset, providers: Sequence[str]) -> list[ProviderResult]:
    results: list[ProviderResult] = []
    for p in providers:
        est = cost_mod.estimate(
            p,
            add_calls=len(dataset.facts),
            search_calls=len(dataset.queries),
            delete_calls=len(dataset.facts) if cfg.cleanup else 0,
        )
        pr = ProviderResult(
            provider=p,
            status="skipped",
            skip_reason="dry-run",
            estimated_cost_usd=est.estimated_usd,
            metrics=Metrics(),
        )
        results.append(pr)
    return results


def run(
    cfg: RunConfig,
    dataset: Dataset,
    *,
    memory_factory: Optional[Callable[[str], Any]] = None,
) -> list[ProviderResult]:
    """Execute the eval run and return per-provider results."""
    factory = memory_factory or _default_factory

    if cfg.dry_run:
        return _build_dry_run(cfg, dataset, cfg.providers)

    results: list[ProviderResult] = []
    gold = {q.query_id: q.gold_fact_ids for q in dataset.queries}

    with TraceWriter(cfg.trace_path) as trace:
        for provider in cfg.providers:
            pr = _run_provider(provider, cfg, dataset, gold, factory, trace)
            results.append(pr)

    return results


def _run_provider(
    provider: str,
    cfg: RunConfig,
    dataset: Dataset,
    gold: dict[str, tuple[str, ...]],
    factory: Callable[[str], Any],
    trace: TraceWriter,
) -> ProviderResult:
    pr = ProviderResult(provider=provider)
    run_started = time.perf_counter()
    try:
        mem = factory(provider)
    except Exception as exc:  # pragma: no cover - defensive
        pr.status = "failed"
        pr.errors.append(
            ErrorRecord(
                verb="init",
                error_class=type(exc).__name__,
                message=str(exc),
                ts=_iso_now(),
            )
        )
        pr.metrics = Metrics(error_count=1)
        trace.emit(provider=provider, verb="init", run_id=cfg.run_id,
                   latency_ms=0.0, error=f"{type(exc).__name__}: {exc}")
        return pr

    # ---- ingest ----
    added_ids: list[str] = []
    for fact in dataset.facts:
        t0 = time.perf_counter()
        err: Optional[str] = None
        try:
            rec = mem.add(
                content=_stamp(fact.fact_id, fact.content),
                user_id=cfg.user_id(),
                tags=[*fact.tags, f"fact:{fact.fact_id}"],
            )
            rid = getattr(rec, "id", None)
            if rid:
                added_ids.append(rid)
        except Exception as exc:
            err = f"{type(exc).__name__}: {exc}"
            pr.errors.append(
                ErrorRecord(
                    verb="add",
                    error_class=type(exc).__name__,
                    message=str(exc),
                    ts=_iso_now(),
                    fact_id=fact.fact_id,
                )
            )
        finally:
            latency_ms = (time.perf_counter() - t0) * 1000
            pr.ingest_latencies_ms.append(latency_ms)
            trace.emit(
                provider=provider, verb="add", run_id=cfg.run_id,
                latency_ms=latency_ms,
                payload_text=fact.content,
                error=err,
                extra={"fact_id": fact.fact_id},
            )

    # ---- wait for ingest (no-op for sync adapters; polling for async) ----
    wait = getattr(mem, "wait_for_ingest", None)
    if callable(wait) and added_ids:
        try:
            wait(added_ids, cfg.user_id(), timeout=30.0)
        except Exception:  # pragma: no cover - defensive
            pass

    # ---- search ----
    for query in dataset.queries:
        t0 = time.perf_counter()
        top_ids: list[str] = []
        err: Optional[str] = None
        try:
            hits = mem.search(query.query, cfg.user_id(), limit=cfg.top_k)
            for h in hits:
                fid = _recover_fact_id(
                    content=getattr(h.memory, "content", None),
                    tags=getattr(h.memory, "tags", None),
                )
                if fid:
                    top_ids.append(fid)
        except Exception as exc:
            err = f"{type(exc).__name__}: {exc}"
            pr.errors.append(
                ErrorRecord(
                    verb="search",
                    error_class=type(exc).__name__,
                    message=str(exc),
                    ts=_iso_now(),
                    query_id=query.query_id,
                )
            )
        latency_ms = (time.perf_counter() - t0) * 1000
        pr.search_latencies_ms.append(latency_ms)
        pr.query_results.append(
            QueryResult(
                query_id=query.query_id,
                top_k_fact_ids=top_ids,
                latency_ms=latency_ms,
                error=err,
            )
        )
        trace.emit(
            provider=provider, verb="search", run_id=cfg.run_id,
            latency_ms=latency_ms,
            payload_text=query.query,
            result_count=len(top_ids),
            error=err,
            extra={"query_id": query.query_id},
        )

    pr.total_wall_s = round(time.perf_counter() - run_started, 3)
    pr.metrics = Metrics(error_count=len(pr.errors))
    pr.metrics = compute_metrics(pr, gold)
    # carry over error_count after compute_metrics overwrites
    pr.metrics.error_count = len(pr.errors)

    # cost accounting (only matters when --live and provider is paid)
    est = cost_mod.estimate(
        provider,
        add_calls=len(dataset.facts),
        search_calls=len(dataset.queries),
        delete_calls=len(dataset.facts) if cfg.cleanup else 0,
    )
    pr.estimated_cost_usd = est.estimated_usd

    # status roll-up
    if pr.errors and pr.query_results and any(qr.top_k_fact_ids for qr in pr.query_results):
        pr.status = "partial"
    elif pr.errors and not any(qr.top_k_fact_ids for qr in pr.query_results):
        pr.status = "failed"
    else:
        pr.status = "ok"

    return pr


def _iso_now() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


__all__ = ["run", "_FACT_PREFIX_RE", "_stamp", "_recover_fact_id"]
