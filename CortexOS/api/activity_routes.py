"""One read-only control-plane view over everything the engine is doing.

GET /api/engine/activity — the single endpoint an AirGPT "Engine activity"
panel needs: routines (running / paused / due-soon / budget), background
workflows (active + recent), racing families, and apps awaiting approval.
Sections are fault-isolated: one broken store reports its error inline and
never takes the panel down with it.
"""

from __future__ import annotations

import time
from typing import Any, Callable


def _section(fn: Callable[[], dict[str, Any]]) -> dict[str, Any]:
    try:
        return fn()
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {exc}"}


def register_activity_routes(app: Any) -> None:
    @app.get("/api/engine/activity")
    async def engine_activity() -> dict[str, Any]:
        now = time.time()

        def _routines() -> dict[str, Any]:
            from CortexOS.execution import routine_scheduler as rs

            rs.init()
            rows = rs.list_routines()
            due = sorted(
                (r for r in rows if r["enabled"] and r["status"] != "paused"),
                key=lambda r: r.get("next_run_at") or 0,
            )
            return {
                "total": len(rows),
                "running": [r["id"] for r in rows if r["status"] == "running"],
                "paused": sum(1 for r in rows if r["status"] == "paused"),
                "due_soon": [
                    {
                        "id": r["id"],
                        "name": r["name"],
                        "in_seconds": max(0, int((r.get("next_run_at") or now) - now)),
                    }
                    for r in due[:5]
                ],
                "budget": rs.global_budget_state(),
            }

        def _workflows() -> dict[str, Any]:
            from CortexOS.execution import workflow_store

            workflow_store.init()

            def _slim(run: dict[str, Any]) -> dict[str, Any]:
                return {
                    key: run.get(key)
                    for key in ("id", "template_id", "title", "status", "created_at")
                }

            return {
                "active": [_slim(r) for r in workflow_store.list_runs(limit=10, status="active")],
                "recent": [_slim(r) for r in workflow_store.list_runs(limit=5)],
            }

        def _races() -> dict[str, Any]:
            from CortexOS.execution import scoreboard

            scoreboard.init()
            return {"families": scoreboard.list_families()[:10]}

        def _apps() -> dict[str, Any]:
            from CortexOS.execution import app_store

            app_store.init()
            rows = app_store.list_apps()
            return {
                "pending_drafts": [
                    {"id": a["id"], "name": a["name"], "stack": a["stack"]}
                    for a in rows
                    if a["status"] == "draft"
                ],
                "blocked": sum(1 for a in rows if a["status"] == "blocked"),
                "approved": sum(1 for a in rows if a["status"] == "approved"),
                "running": app_store.list_running(),
            }

        return {
            "ok": True,
            "ts": now,
            "routines": _section(_routines),
            "workflows": _section(_workflows),
            "races": _section(_races),
            "apps": _section(_apps),
        }
