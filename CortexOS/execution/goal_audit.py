"""F1 ledger wiring for the goal / seek / ethical-gate path.

Every autonomous decision the engine makes about a goal lands on the same
append-only F1 ledger the rest of the stack uses. Two policies are deliberate
and tested:

**Identifiers and verdicts, never content.** Goal statements, proposal titles
and the "why" text are user- and business-authored prose. The ledger records
*what was decided* — goal id, action kind, verdict, which constraint kinds were
breached, how many predicates passed — never the prose itself. An audit trail
should prove behaviour without becoming a second copy of the data.

**A failed audit write is reported, never swallowed.** The seeker must not
crash because a ledger is unavailable, but a compliance record that silently
did not happen is worse than a visible error. Callers get `{"ok": false,
"error": ...}` and surface it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

# None → the DMS pack's default ledger location. Tests point this at a tmp file.
LEDGER_DB_PATH: Path | str | None = None

ACTOR = "cortex.engine"

EVENT_GOAL_BOUND = "goal.bound"
EVENT_GOAL_UPDATED = "goal.updated"
EVENT_SEEK = "engine.seek"
EVENT_GATE_DENIED = "goal.gate_denied"
EVENT_TERMINATION_BLOCKED = "goal.termination_blocked"


def _append(event_type: str, payload: dict[str, Any]) -> dict[str, Any]:
    try:
        from CortexOS.audit import resolve_ledger

        db_path = None if LEDGER_DB_PATH is None else str(LEDGER_DB_PATH)
        entry = resolve_ledger().append(ACTOR, event_type, payload, db_path=db_path)
        return {"ok": True, "event": event_type, "seq": getattr(entry, "seq", None)}
    except Exception as exc:  # never crash the engine on an audit outage…
        return {"ok": False, "event": event_type, "error": f"{type(exc).__name__}: {exc}"}


def _predicate_summary(predicate_results: list[dict[str, Any]] | None) -> dict[str, Any]:
    results = predicate_results or []
    return {
        "count": len(results),
        "passed": sum(1 for p in results if p.get("pass")),
        "types": sorted({str(p.get("type") or "unknown") for p in results}),
    }


def goal_bound(goal: dict[str, Any]) -> dict[str, Any]:
    return _append(
        EVENT_GOAL_BOUND,
        {
            "goal_id": goal.get("id"),
            "org_id": goal.get("org_id") or "",
            "criteria_count": len(goal.get("measurable_criteria") or []),
            "constraint_kinds": sorted(
                c.get("kind", "") for c in goal.get("hard_constraints") or []
            ),
            "audit_required": bool(goal.get("audit_required")),
            "autonomy_level": (goal.get("soft_preferences") or {}).get("autonomy_level"),
        },
    )


def goal_updated(goal: dict[str, Any], changed: list[str]) -> dict[str, Any]:
    return _append(
        EVENT_GOAL_UPDATED,
        {
            "goal_id": goal.get("id"),
            "changed_fields": sorted(changed),
            "constraint_kinds": sorted(
                c.get("kind", "") for c in goal.get("hard_constraints") or []
            ),
            "autonomy_level": (goal.get("soft_preferences") or {}).get("autonomy_level"),
        },
    )


def seek_recorded(
    goal_id: str,
    proposals: list[dict[str, Any]],
    *,
    trigger: str,
    blocked: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Proves the engine acted on its own, and that nothing self-authorised."""
    return _append(
        EVENT_SEEK,
        {
            "goal_id": goal_id,
            "initiative": "proactive",
            "trigger": trigger,
            "proposal_count": len(proposals),
            "action_kinds": sorted({str(p.get("action") or "") for p in proposals}),
            "requires_confirm": sum(1 for p in proposals if p.get("requires_confirm")),
            "auto_ok": sum(1 for p in proposals if p.get("auto_ok")),
            "blocked_count": len(blocked or []),
        },
    )


def gate_denied(
    goal_id: str,
    action_kind: str,
    verdict: dict[str, Any],
    *,
    predicate_results: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return _append(
        EVENT_GATE_DENIED,
        {
            "goal_id": goal_id,
            "action_kind": action_kind,
            "risk": verdict.get("risk"),
            "blocked_by": verdict.get("blocked_by") or [],
            "predicate_results": _predicate_summary(predicate_results),
        },
    )


def termination_blocked(
    goal_id: str,
    outcome: dict[str, Any],
    *,
    predicate_results: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """`false_pass_caught` / `constraint_violated` are the events that prove the
    engine refused to call a confident-but-wrong run a success."""
    return _append(
        EVENT_TERMINATION_BLOCKED,
        {
            "goal_id": goal_id,
            "verdict": outcome.get("verdict"),
            "success": bool(outcome.get("success")),
            "collapse": round(float(outcome.get("collapse") or 0.0), 6),
            "blocked_by": outcome.get("blocked_by") or [],
            "predicate_results": _predicate_summary(predicate_results),
        },
    )
