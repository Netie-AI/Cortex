"""DMS stress harness v0 — ledger append storm + NL->SQL query concurrency + discovery.

Report-only (no pytest gate yet): prints latency percentiles and integrity
results, writes JSON next to the accuracy report. Full 6-part suite (ingest
throughput, chaos-lite kill/resume, soak, k6 API load) is specced in
docs/dms/BUILD_PLAN_V2_LAKEHOUSE.md track B.

CLI:  python -m bench.stress [--scenario ledger|query|stream|discovery|activity|routines|apps|all]
      [--threads 8] [--iterations 25] [--json PATH]
"""
from __future__ import annotations

import argparse
import datetime as _dt
import io
import json
import os
import statistics
import tempfile
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
RESULTS_DIR = ROOT / "bench" / "results"

_STRESS_SCENARIOS = (
    "ledger",
    "query",
    "stream",
    "discovery",
    "activity",
    "routines",
    "apps",
    "all",
)

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


def stress_discovery(threads: int, iterations: int) -> dict[str, Any]:
    """Concurrent Find Skills queries — ranking must stay error-free under load."""
    from CortexOS.discovery.find import find_skills, find_mcp

    goals = (
        "playwright e2e testing",
        "PDF extraction skill",
        "github mcp server",
        "code review subagent",
        "security audit",
        "sqlite database tools",
    )

    def one(i: int) -> float | None:
        goal = goals[i % len(goals)]
        t0 = time.perf_counter()
        try:
            if i % 3 == 0:
                res = find_mcp(goal, top_k=5)
            else:
                res = find_skills(goal, top_k=5)
            if not res.get("ok"):
                return None
            if not res.get("matches"):
                return None
            return (time.perf_counter() - t0) * 1000
        except Exception:
            return None

    t_start = time.perf_counter()
    latencies, errors = _run_pool(one, threads=threads, iterations=iterations)
    elapsed = time.perf_counter() - t_start
    return {
        "scenario": "discovery_find_skills_storm",
        "threads": threads,
        "queries_attempted": threads * iterations,
        "queries_ok": len(latencies),
        "errors": errors,
        "throughput_per_s": round(len(latencies) / elapsed, 1) if elapsed else 0.0,
        **_percentiles(latencies),
    }


def _make_stress_client(home: Path):
    """In-process FastAPI client with isolated engine DBs under *home*."""
    os.environ["PACK"] = "dms"
    os.environ["DMS_AUTH_DISABLED"] = "1"
    from CortexOS.execution import app_store, routine_scheduler, scoreboard, workflow_store
    from CortexOS.api.app import create_app
    from fastapi.testclient import TestClient

    scoreboard.DB_PATH = home / "scoreboard.db"
    routine_scheduler.DB_PATH = home / "routines.db"
    app_store.DB_PATH = home / "apps.db"
    app_store.APPS_ROOT = home / "apps"
    workflow_store.DB_PATH = home / "wf-runs.db"
    scoreboard.init()
    routine_scheduler.init()
    app_store.init()
    workflow_store.init()
    return TestClient(create_app())


def stress_activity(threads: int, iterations: int) -> dict[str, Any]:
    """Concurrent GET /api/engine/activity — panel must stay up under load."""
    home = Path(tempfile.mkdtemp(prefix="dms_activity_stress_"))
    client = _make_stress_client(home)
    client.post(
        "/api/routines",
        json={"name": "stress-act", "prompt": "noop", "interval_seconds": 3600},
    )

    def one(_i: int) -> float | None:
        t0 = time.perf_counter()
        try:
            resp = client.get("/api/engine/activity")
            if resp.status_code != 200:
                return None
            body = resp.json()
            if not body.get("ok"):
                return None
            for key in ("routines", "workflows", "races", "apps"):
                if key not in body:
                    return None
            return (time.perf_counter() - t0) * 1000
        except Exception:
            return None

    t_start = time.perf_counter()
    latencies, errors = _run_pool(one, threads=threads, iterations=iterations)
    elapsed = time.perf_counter() - t_start
    return {
        "scenario": "engine_activity_storm",
        "threads": threads,
        "queries_attempted": threads * iterations,
        "queries_ok": len(latencies),
        "errors": errors,
        "throughput_per_s": round(len(latencies) / elapsed, 1) if elapsed else 0.0,
        **_percentiles(latencies),
    }


def stress_routines(threads: int, iterations: int) -> dict[str, Any]:
    """Concurrent tick / run_once under governor + lease (temp routines.db)."""
    import asyncio

    from CortexOS.execution import routine_scheduler as rs

    home = Path(tempfile.mkdtemp(prefix="dms_routines_stress_"))
    rs.DB_PATH = home / "routines.db"
    rs.init()
    created = rs.create_routine(name="stress-r", prompt="hello", interval_seconds=1)
    rid = created["id"]
    loop_lock = __import__("threading").Lock()

    def one(i: int) -> float | None:
        t0 = time.perf_counter()
        try:
            with loop_lock:
                if i % 2 == 0:
                    asyncio.run(rs.tick())
                else:
                    asyncio.run(rs.run_once(rid))
            return (time.perf_counter() - t0) * 1000
        except Exception:
            return None

    t_start = time.perf_counter()
    latencies, errors = _run_pool(one, threads=threads, iterations=iterations)
    elapsed = time.perf_counter() - t_start
    row = rs.get_routine(rid)
    # Must never wedge forever in 'running' after the storm.
    wedged = bool(row and row.get("status") == "running")
    if wedged:
        errors += 1
    return {
        "scenario": "routines_tick_run_storm",
        "threads": threads,
        "ops_attempted": threads * iterations,
        "ops_ok": len(latencies),
        "errors": errors,
        "wedged_running": wedged,
        "throughput_per_s": round(len(latencies) / elapsed, 1) if elapsed else 0.0,
        **_percentiles(latencies),
    }


def _static_zip_bytes() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("index.html", "<!doctype html><title>stress</title><h1>ok</h1>")
    return buf.getvalue()


def stress_apps(threads: int, iterations: int) -> dict[str, Any]:
    """Sequential-ish import→approve→start→HTTP probe→stop on static fixtures.

    Threads are capped: each iteration owns one port; true parallel start of many
    apps would thrash the 88xx pool. We still use a pool but serialize via a lock
    inside each worker's full lifecycle so errors surface under contention on the DB.
    """
    import threading
    import urllib.request

    from CortexOS.execution import app_store

    home = Path(tempfile.mkdtemp(prefix="dms_apps_stress_"))
    app_store.DB_PATH = home / "apps.db"
    app_store.APPS_ROOT = home / "apps"
    app_store.init()
    gate = threading.Lock()
    blob = _static_zip_bytes()

    def one(i: int) -> float | None:
        t0 = time.perf_counter()
        app_id = None
        try:
            with gate:
                imported = app_store.import_zip_bytes(blob, name=f"stress-{i}")
                if not imported.get("ok"):
                    return None
                app_id = imported["app"]["id"]
                approved = app_store.approve(app_id)
                if not approved.get("ok"):
                    return None
                started = app_store.start_app(app_id)
                if not started.get("ok"):
                    return None
                port = started["app"]["port"]
            # Probe outside the lock so other workers can import meanwhile.
            url = f"http://127.0.0.1:{port}/"
            deadline = time.time() + 15
            ok = False
            while time.time() < deadline:
                try:
                    with urllib.request.urlopen(url, timeout=2) as resp:
                        if resp.status == 200:
                            ok = True
                            break
                except Exception:
                    time.sleep(0.2)
            if not ok:
                return None
            with gate:
                stopped = app_store.stop_app(app_id)
                if not stopped.get("ok"):
                    return None
                app_store.delete_app(app_id)
            return (time.perf_counter() - t0) * 1000
        except Exception:
            if app_id:
                try:
                    app_store.stop_app(app_id)
                    app_store.delete_app(app_id)
                except Exception:
                    pass
            return None

    # Keep concurrency modest — one live static server per worker is enough storm.
    worker_threads = max(1, min(threads, 2))
    t_start = time.perf_counter()
    latencies, errors = _run_pool(one, threads=worker_threads, iterations=iterations)
    elapsed = time.perf_counter() - t_start
    return {
        "scenario": "apps_start_stop_storm",
        "threads": worker_threads,
        "cycles_attempted": worker_threads * iterations,
        "cycles_ok": len(latencies),
        "errors": errors,
        "throughput_per_s": round(len(latencies) / elapsed, 1) if elapsed else 0.0,
        **_percentiles(latencies),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--scenario",
        default="all",
        choices=list(_STRESS_SCENARIOS),
    )
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
    if args.scenario in ("discovery", "all"):
        scenarios.append(stress_discovery(args.threads, args.iterations))
    if args.scenario in ("activity", "all"):
        scenarios.append(stress_activity(args.threads, args.iterations))
    if args.scenario in ("routines", "all"):
        scenarios.append(stress_routines(args.threads, args.iterations))
    if args.scenario in ("apps", "all"):
        scenarios.append(stress_apps(args.threads, args.iterations))

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
