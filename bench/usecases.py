"""Deterministic use-case benchmark across the five Cortex surfaces.

Zero-LLM by construction: every case exercises the engine's deterministic
paths, so a full run costs 0 tokens, finishes in seconds, and is replicable on
any machine — exactly the "cheap, replicable, most token-saving" bar for a
standing benchmark. Records per-case pass/latency to
data/bench/usecases_report.{json,md}.

Run:  python -m bench.usecases
"""

from __future__ import annotations

import asyncio
import io
import json
import time
import zipfile
from pathlib import Path
from typing import Any, Callable

from CortexOS.paths import data_path


def _case(surface: str, name: str, fn: Callable[[], Any]) -> dict[str, Any]:
    started = time.perf_counter()
    try:
        detail = fn() or {}
        ok = bool(detail.get("ok", True)) if isinstance(detail, dict) else True
        note = str(detail.get("note", "")) if isinstance(detail, dict) else ""
    except Exception as exc:
        ok, note = False, f"{type(exc).__name__}: {exc}"
    return {
        "surface": surface,
        "case": name,
        "ok": ok,
        "latency_ms": int((time.perf_counter() - started) * 1000),
        "tokens": 0,
        "replicable": True,
        "note": note,
    }


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


def run_all(*, state_dir: Path | None = None, write: bool = True) -> dict[str, Any]:
    from CortexOS.execution import app_store, race_router, routine_scheduler, scoreboard
    from CortexOS.execution import workflow_runner
    from CortexOS.execution.gen_cfsm import iterate_cfsm
    from CortexOS.execution.preset_router import plan_for_request
    from CortexOS.execution.run_plan import execute_run_plan
    from CortexOS.execution.workflow_recognizer import recognize
    from CortexOS.context_engineering.assembler import ContextRequest, assemble_context
    from CortexOS.memory.semantic_cache import SemanticCache

    base = Path(state_dir) if state_dir else data_path("bench", "state")
    base.mkdir(parents=True, exist_ok=True)
    saved = (
        scoreboard.DB_PATH,
        routine_scheduler.DB_PATH,
        app_store.DB_PATH,
        app_store.APPS_ROOT,
    )
    scoreboard.DB_PATH = base / "scoreboard.db"
    routine_scheduler.DB_PATH = base / "routines.db"
    app_store.DB_PATH = base / "apps.db"
    app_store.APPS_ROOT = base / "apps"

    cases: list[dict[str, Any]] = []
    try:
        # --- DMS: governed engine runs over the one DAG runtime ---------------
        for preset in ("minimal", "sequential", "dag"):
            cases.append(
                _case(
                    "DMS",
                    f"engine_run_{preset}",
                    lambda p=preset: {
                        "ok": _run(
                            execute_run_plan(
                                plan_for_request(p, {"prompt": "count pallets in dock 2"}),
                                {"prompt": "count pallets in dock 2"},
                            )
                        )["ok"]
                    },
                )
            )

        # --- AirGPT: RAG template, context assembly, memory cache -------------
        def _rag() -> dict[str, Any]:
            body = {
                "prompt": "what is netie",
                "depth": "basic",
                "rag_corpus": ["netie is the cortex engine", "airgpt is the driver app"],
            }
            out = _run(execute_run_plan(plan_for_request("rag", body), body))
            return {"ok": bool(out.get("ok")), "note": f"runner={out.get('runner')}"}

        cases.append(_case("AirGPT", "rag_basic_small_corpus", _rag))

        def _ctx() -> dict[str, Any]:
            out = assemble_context(
                ContextRequest(
                    instructions="Answer briefly; never invent order numbers.",
                    messages=[f"turn {i}: filler chatter about pallets" for i in range(30)],
                    token_budget=800,
                )
            )
            return {
                "ok": out.token_estimate <= 900 and "never invent" in out.system,
                "note": f"tokens={out.token_estimate} compacted={out.compacted}",
            }

        cases.append(_case("AirGPT", "context_assemble_800_budget", _ctx))

        def _memory() -> dict[str, Any]:
            cache = SemanticCache()
            cache.put([1.0, 0.0, 0.0], "cached answer")
            hit = cache.get([1.0, 0.0, 0.0])
            miss = cache.get([0.0, 1.0, 0.0])
            return {"ok": hit == "cached answer" and miss is None}

        cases.append(_case("AirGPT", "semantic_cache_roundtrip", _memory))

        # --- Agentic creator: race, gen-cFSM, app ship gate --------------------
        cases.append(
            _case(
                "AgenticCreator",
                "race_auto_cold_top3",
                lambda: {
                    "ok": _run(
                        race_router.auto_route(
                            "bench: fetch and summarize data",
                            {"prompt": "hello"},
                            predicates=[{"type": "nonempty"}],
                            min_runs=1,
                        )
                    )["ok"]
                },
            )
        )
        cases.append(
            _case(
                "AgenticCreator",
                "gen_cfsm_iterate_h3",
                lambda: {
                    "ok": _run(
                        iterate_cfsm(
                            "bench goal",
                            {"prompt": "hello"},
                            predicates=[{"type": "nonempty"}],
                            record=False,
                        )
                    )["ok"]
                },
            )
        )

        def _apps() -> dict[str, Any]:
            buffer = io.BytesIO()
            with zipfile.ZipFile(buffer, "w") as zf:
                zf.writestr("main.py", "print('hi')")
            imported = app_store.import_zip_bytes(buffer.getvalue(), name="bench-app")
            if not (imported.get("ok") and imported["app"]["status"] == "draft"):
                return {"ok": False, "note": "import not draft"}
            approved = app_store.approve(imported["app"]["id"])
            return {
                "ok": bool(approved.get("ok")),
                "note": f"port={approved.get('app', {}).get('port')}",
            }

        cases.append(_case("AgenticCreator", "app_import_gate_approve", _apps))

        # --- Scheduler: routines + governor ------------------------------------
        def _routine() -> dict[str, Any]:
            routine_scheduler.init()
            routine = routine_scheduler.create_routine(
                "bench", "hello", interval_seconds=3600
            )
            run = _run(routine_scheduler.run_once(routine["id"]))
            due_after = _run(routine_scheduler.tick())
            return {"ok": bool(run["ok"]) and due_after == []}

        cases.append(_case("Scheduler", "routine_create_run_tick", _routine))

        def _governor() -> dict[str, Any]:
            routine = routine_scheduler.create_routine(
                "bench-broken", "x", preset="langgraph", interval_seconds=0
            )
            for _ in range(routine_scheduler.GOVERNOR_ERROR_STREAK):
                _run(routine_scheduler.tick())
            state = routine_scheduler.get_routine(routine["id"])
            return {"ok": state["status"] == "paused", "note": state["paused_reason"]}

        cases.append(_case("Scheduler", "governor_auto_pause", _governor))

        # --- OpenIDE: workflow surface -----------------------------------------
        cases.append(
            _case(
                "OpenIDE",
                "workflow_templates_list",
                lambda: {"ok": len(workflow_runner.list_workflows()) > 0},
            )
        )

        def _recognize() -> dict[str, Any]:
            rec = recognize(
                "deep research: the malaysian warehouse robotics market in the background"
            )
            template = getattr(rec, "template_id", "") or ""
            return {"ok": bool(template), "note": template}

        cases.append(_case("OpenIDE", "workflow_recognize_research", _recognize))
    finally:
        scoreboard.DB_PATH, routine_scheduler.DB_PATH, app_store.DB_PATH, app_store.APPS_ROOT = saved

    report = {
        "total": len(cases),
        "passed": sum(1 for c in cases if c["ok"]),
        "token_cost": 0,
        "cases": cases,
        "recommendation": (
            "Every case is deterministic and free — adopt as the standing "
            "regression benchmark; add LLM-tier cases only behind explicit opt-in."
        ),
    }
    if write:
        out_dir = data_path("bench")
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "usecases_report.json").write_text(
            json.dumps(report, indent=2), encoding="utf-8"
        )
        lines = [
            "# Cortex use-case benchmark — deterministic, 0 tokens",
            "",
            f"**{report['passed']}/{report['total']} passed** · token cost 0 · replicable on any machine",
            "",
            "| surface | case | ok | ms | note |",
            "|---|---|---|---|---|",
        ]
        lines.extend(
            f"| {c['surface']} | {c['case']} | {'✅' if c['ok'] else '❌'} | {c['latency_ms']} | {c['note']} |"
            for c in cases
        )
        (out_dir / "usecases_report.md").write_text("\n".join(lines), encoding="utf-8")
    return report


if __name__ == "__main__":
    result = run_all()
    print(f"{result['passed']}/{result['total']} passed · 0 tokens")
    for case in result["cases"]:
        if not case["ok"]:
            print(f"FAIL {case['surface']}/{case['case']}: {case['note']}")
