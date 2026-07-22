"""DMS stress harness v0 — ledger append storm + NL->SQL query concurrency.

Report-only (no pytest gate yet): prints latency percentiles and integrity
results, writes JSON next to the accuracy report. Full 6-part suite (ingest
throughput, chaos-lite kill/resume, soak, k6 API load) is specced in
docs/dms/BUILD_PLAN_V2_LAKEHOUSE.md track B.

CLI:  python -m bench.stress [--scenario ledger|query|all]
      [--threads 8] [--iterations 25] [--json PATH]
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import statistics
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
RESULTS_DIR = ROOT / "bench" / "results"

QUERY_MIX = (
    "How many SKUs do we have in inventory?",
    "Top 5 selling SKUs by revenue",
    "Show warehouse capacity utilisation",
    "Rank suppliers by combined risk and lead time score",
    "List active alerts across the warehouse network",
    "What is total stock value by category?",
)


def _percentiles(samples_ms: list[float]) -> dict[str, float]:
    if not samples_ms:
        return {"p50_ms": 0.0, "p95_ms": 0.0, "max_ms": 0.0}
    ordered = sorted(samples_ms)
    q = statistics.quantiles(ordered, n=20, method="inclusive") if len(ordered) > 1 else ordered
    return {
        "p50_ms": round(statistics.median(ordered), 1),
        "p95_ms": round(q[-1] if len(ordered) > 1 else ordered[0], 1),
        "max_ms": round(ordered[-1], 1),
    }


def _run_pool(fn: Callable[[int], float | None], *, threads: int, iterations: int) -> tuple[list[float], int]:
    """Run fn(i) across a thread pool; returns (latencies_ms, error_count)."""
    latencies: list[float] = []
    errors = 0
    with ThreadPoolExecutor(max_workers=threads) as pool:
        for outcome in pool.map(fn, range(threads * iterations)):
            if outcome is None:
                errors += 1
            else:
                latencies.append(outcome)
    return latencies, errors


def stress_ledger(threads: int, iterations: int) -> dict[str, Any]:
    """Concurrent hash-chained appends into a throwaway SQLite ledger, then verify.

    The chain MUST stay gap-free and valid under contention — this is the F1
    integrity guarantee under storm conditions.
    """
    from packs.dms.audit import ledger

    db_path = Path(tempfile.mkdtemp(prefix="dms_stress_")) / "ledger.sqlite"

    def one(i: int) -> float | None:
        t0 = time.perf_counter()
        try:
            ledger.append("stress", "bench.append", {"i": i}, db_path=db_path)
            return (time.perf_counter() - t0) * 1000
        except Exception:
            return None

    t_start = time.perf_counter()
    latencies, errors = _run_pool(one, threads=threads, iterations=iterations)
    elapsed = time.perf_counter() - t_start
    verify = ledger.verify(db_path=db_path)

    total = threads * iterations
    return {
        "scenario": "ledger_append_storm",
        "threads": threads,
        "appends_attempted": total,
        "appends_ok": len(latencies),
        "errors": errors,
        "throughput_per_s": round(len(latencies) / elapsed, 1) if elapsed else 0.0,
        "chain_valid": bool(verify.ok),
        "broken_at": verify.broken_at,
        **_percentiles(latencies),
    }


def stress_queries(threads: int, iterations: int) -> dict[str, Any]:
    """Concurrent NL->SQL answer_question calls over the demo DuckDB."""
    from bench.accuracy import _ensure_db_loaded
    from CortexOS.dms.query_service import answer_question

    _ensure_db_loaded()

    def one(i: int) -> float | None:
        question = QUERY_MIX[i % len(QUERY_MIX)]
        t0 = time.perf_counter()
        try:
            result = answer_question(question)
            if result.get("route") not in ("sql", "rag"):
                return None
            return (time.perf_counter() - t0) * 1000
        except Exception:
            return None

    t_start = time.perf_counter()
    latencies, errors = _run_pool(one, threads=threads, iterations=iterations)
    elapsed = time.perf_counter() - t_start

    return {
        "scenario": "nl_sql_query_concurrency",
        "threads": threads,
        "queries_attempted": threads * iterations,
        "queries_ok": len(latencies),
        "errors": errors,
        "throughput_per_s": round(len(latencies) / elapsed, 1) if elapsed else 0.0,
        **_percentiles(latencies),
    }


def stress_streams(threads: int, iterations: int) -> dict[str, Any]:
    """Concurrent webhook batches into a stream buffer → bronze; measures rows/s."""
    import tempfile

    from packs.dms.streams import buffer

    home = Path(tempfile.mkdtemp(prefix="dms_stream_stress_"))
    import os

    os.environ["DMS_LAKEHOUSE_HOME"] = str(home)
    os.environ.setdefault("DMS_STREAM_BATCH", "200")
    buffer._BUFFERS.clear()
    buffer.reset_writer()
    batch = 20

    def one(i: int) -> float | None:
        t0 = time.perf_counter()
        try:
            buffer.append_events("stress", [{"event_id": f"{i}-{j}", "v": j} for j in range(batch)])
            return (time.perf_counter() - t0) * 1000
        except Exception:
            return None

    t_start = time.perf_counter()
    latencies, errors = _run_pool(one, threads=threads, iterations=iterations)
    buffer.flush("stress")
    elapsed = time.perf_counter() - t_start
    total_events = len(latencies) * batch

    return {
        "scenario": "stream_ingest_throughput",
        "threads": threads,
        "batches_ok": len(latencies),
        "events": total_events,
        "errors": errors,
        "events_per_s": round(total_events / elapsed, 1) if elapsed else 0.0,
        **_percentiles(latencies),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario", default="all", choices=["ledger", "query", "stream", "all"])
    parser.add_argument("--threads", type=int, default=8)
    parser.add_argument("--iterations", type=int, default=25)
    parser.add_argument("--json", default=None)
    args = parser.parse_args()

    scenarios: list[dict[str, Any]] = []
    if args.scenario in ("ledger", "all"):
        scenarios.append(stress_ledger(args.threads, args.iterations))
    if args.scenario in ("query", "all"):
        scenarios.append(stress_queries(args.threads, args.iterations))
    if args.scenario in ("stream", "all"):
        scenarios.append(stress_streams(args.threads, args.iterations))

    report = {
        "generated_at": _dt.datetime.now(_dt.timezone.utc).isoformat(),
        "scenarios": scenarios,
    }
    for s in scenarios:
        print(json.dumps(s, indent=2))

    out = Path(args.json) if args.json else RESULTS_DIR / "stress_last_run.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"\nJSON report: {out}")


if __name__ == "__main__":
    main()
