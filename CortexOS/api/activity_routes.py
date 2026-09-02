"""One read-only control-plane view over everything the engine is doing.

GET /api/engine/activity -- the single endpoint an AirGPT "Engine activity"
panel needs: routines (running / paused / due-soon / budget), background
workflows (active + recent), racing families, apps awaiting approval, and
governance (ledger tip, bound sessions, refusals; no payloads).
Sections are fault-isolated: one broken store reports its error inline and
never takes the panel down with it.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any


def _section(fn: Callable[[], dict[str, Any]]) -> dict[str, Any]:
    try:
        return fn()
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {exc}"}


_REFUSAL_MARKERS = ("denied", "blocked", "refused")
_LEDGER_PAGE = 500
_LEDGER_WALK_CAP = 20_000
_GOV_RECENT = 8
_GOV_WINDOW = 50


def _entry_seq(row: Any) -> int:
    if hasattr(row, "seq"):
        return int(row.seq)
    return int(row["seq"])


def _public_entry(row: Any) -> dict[str, Any]:
    """Identifiers and verdicts only. Payloads stay off the control panel."""
    if hasattr(row, "seq"):
        return {
            "seq": int(row.seq),
            "event_type": str(row.event_type),
            "actor": str(row.actor),
        }
    return {
        "seq": int(row["seq"]),
        "event_type": str(row["event_type"]),
        "actor": str(row["actor"]),
    }


def _is_refusal(event_type: str) -> bool:
    lowered = event_type.lower()
    return any(marker in lowered for marker in _REFUSAL_MARKERS)


def _walk_ledger(writer: Any) -> tuple[int | None, list[Any]]:
    from_seq = 0
    last: list[Any] = []
    seen = 0
    while seen < _LEDGER_WALK_CAP:
        try:
            page = list(writer.list_entries(from_seq=from_seq, limit=_LEDGER_PAGE) or [])
        except TypeError:
            page = list(writer.list_entries() or [])
            return (_entry_seq(page[-1]) if page else None, page[-_GOV_WINDOW:])
        if not page:
            break
        last = page
        seen += len(page)
        if len(page) < _LEDGER_PAGE:
            break
        from_seq = _entry_seq(page[-1]) + 1
    return (_entry_seq(last[-1]) if last else None, last[-_GOV_WINDOW:])


def _governance_view() -> dict[str, Any]:
    from CortexOS.audit import LedgerNotRegistered, registered_ledger, resolve_ledger
    from CortexOS.execution.session_manifests import get_session_registry

    writer = registered_ledger()
    if writer is None:
        try:
            writer = resolve_ledger()
        except LedgerNotRegistered:
            writer = None

    if writer is None:
        ledger: dict[str, Any] = {"registered": False, "tip_seq": None, "recent": []}
        window: list[dict[str, Any]] = []
    else:
        tip, rows = _walk_ledger(writer)
        window = [_public_entry(row) for row in rows]
        ledger = {
            "registered": True,
            "tip_seq": tip,
            "recent": window[-_GOV_RECENT:],
        }

    return {
        "ledger": ledger,
        "manifests": get_session_registry().bound_summary(),
        "refusals": {
            "recent": [row for row in window if _is_refusal(str(row["event_type"]))][
                -_GOV_RECENT:
            ],
        },
    }


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

        def _governance() -> dict[str, Any]:
            return _governance_view()

        return {
            "ok": True,
            "ts": now,
            "routines": _section(_routines),
            "workflows": _section(_workflows),
            "races": _section(_races),
            "apps": _section(_apps),
            "governance": _section(_governance),
        }
