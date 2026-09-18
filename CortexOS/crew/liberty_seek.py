"""LIBERTY-SEEK: G2.1 proactive seeker on the Control+Crew buyer surface.

The engine already has a governed seeker (``CortexOS.execution.seeker``). This
module is a buyer-surface adapter, not a second autonomy stack. Operator start
is Crew POST. Control GET-displays. Proposals are auditable. Execution is
propose-only: execute/auto-run parks. Missing goal or missing extra REFUSE.
JEPA stays the existing cosine / action_value prior — not a trained world
model (#224). Predict-goal is #225. CoT climb leftover (#212) is cited, not
replaced. No live-host invent-green.
"""

from __future__ import annotations

from typing import Any

from CortexOS.execution import enterprise_goal, seeker

LAW = (
    "G2.1 governed proactive seek on Control+Crew. Operator starts seek on "
    "Crew POST /crew/liberty/seek. Control GET-displays this map (F-0030, no "
    "spawn). Seeker proposes; it does not send, publish, buy, deploy or "
    "approve. Gates fail closed: no bound goal REFUSE; execute/auto-run PARK "
    "with executed=[]. JEPA is the existing cosine/action_value prior, not a "
    "trained world model. #224/#225 are other seats. CoT climb #212 stays "
    "INCOMPLETE; DMS #180 gen 57.69% / exact 38.46% WRONG=0 is cited, not "
    "replaced. Not COMPLETE. No live :5000/:8020 CI claim."
)

STATUSES = ("SEEK", "PARK", "REFUSE")
ENGINE_SEEK = "CortexOS.execution.seeker.seek"
EXECUTE = "POST /crew/liberty/seek"
DISPLAY = "GET /crew/liberty"
ENGINE_HTTP = "POST /api/engine/seek"

# Frozen DMS #180 numbers. Do not invent a better climb %.
_MEASURED_BASELINE = {
    "cite": "DMS #180 Formal GREEN @ d2f116a6",
    "gen": "57.69%",
    "exact": "38.46%",
    "wrong": 0,
    "replaces_baseline": False,
    "invented_better": False,
}

_JEPA = {
    "mode": "proxy",
    "trained": False,
    "source": "action_value cosine prior (G2.1/G2.2)",
    "world_model": False,
    "issue_224": "not this seat",
}


def _honesty() -> dict[str, Any]:
    return {
        "complete": False,
        "issue_223_complete": False,
        "issue_222_complete": False,
        "issue_212_complete": False,
        "issue_224": False,
        "issue_225": False,
        "jepa": dict(_JEPA),
        "cot_climb": {
            "status": "INCOMPLETE",
            "complete": False,
            "replaces_baseline": False,
            "invented_better": False,
            "measured_baseline": dict(_MEASURED_BASELINE),
        },
        "measured_baseline": dict(_MEASURED_BASELINE),
        "live_5000_ci": False,
        "live_8020_ci": False,
        "langgraph": False,
        "excel_ppt": "deferred #197 #198 #199",
        "engine": ENGINE_SEEK,
        "control_spawn": False,
    }


def public_map() -> dict[str, Any]:
    """GET map. Control may display. No seek. No live-host claim."""
    bound = None
    try:
        goal = enterprise_goal.active_goal()
    except Exception:
        goal = None
    if goal:
        bound = {"goal_id": goal.get("id"), "statement": goal.get("statement")}
    return {
        "ok": True,
        "law": LAW,
        "execute": EXECUTE,
        "display": DISPLAY,
        "engine_http": ENGINE_HTTP,
        "statuses": list(STATUSES),
        "initiative": "proactive",
        "propose_only": True,
        "agents": (
            "Start liberty seek on Crew. Control is GET-display only. "
            "Proposals are ranked next steps, not executed autonomy. "
            "No bound goal REFUSE. execute=true PARK (executed stays empty)."
        ),
        "bound_goal": bound,
        "vault": "OpenVault keys unused for G2.1 propose-only seek.",
        "freeroute": (
            "Not required for G2.1 propose-only seek. Model hops stay on "
            "Insights/FreeRoute and remain fail-closed when unarmed."
        ),
        **_honesty(),
    }


def last_seek_public(goal_id: str | None = None, *, limit: int = 1) -> dict[str, Any]:
    """Last recorded seek for Control GET-display. Empty is honest, not green."""
    try:
        goal = (
            enterprise_goal.get_goal(goal_id)
            if goal_id
            else enterprise_goal.active_goal()
        )
    except Exception:
        goal = None
    if goal is None:
        return {
            "ok": False,
            "status": "REFUSE",
            "reason": "no_goal_bound",
            "seeks": [],
            "live_5000_ci": False,
            "live_8020_ci": False,
        }
    rows = enterprise_goal.list_seeks(str(goal["id"]), limit=limit)
    return {
        "ok": True,
        "goal_id": goal["id"],
        "seeks": rows,
        "live_5000_ci": False,
        "live_8020_ci": False,
    }


def control_stamp() -> dict[str, Any]:
    """F-0030 GET payload. Never implies Control may POST spawn/run."""
    body = public_map()
    body["display_only"] = True
    body["spawn"] = False
    body["converse"] = False
    body["banner"] = "Display only F-0030"
    body["last"] = last_seek_public()
    return body


def _refuse(reason: str, *, answer: str, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    body = {
        "ok": False,
        "status": "REFUSE",
        "reason": reason,
        "answer": answer,
        "initiative": "proactive",
        "proposals": [],
        "blocked": [],
        "parked": [],
        "executed": [],
        "assumptions": [],
        "audit": None,
        "values": [],
        **_honesty(),
    }
    if extra:
        body.update(extra)
    return body


def _park(
    seek_out: dict[str, Any],
    *,
    reason: str,
    answer: str,
) -> dict[str, Any]:
    proposals = list(seek_out.get("proposals") or [])
    blocked = list(seek_out.get("blocked") or [])
    return {
        "ok": False,
        "status": "PARK",
        "reason": reason,
        "answer": answer,
        "initiative": "proactive",
        "trigger": seek_out.get("trigger") or "liberty",
        "goal_id": seek_out.get("goal_id"),
        "goal_statement": seek_out.get("goal_statement"),
        "proposals": proposals,
        "blocked": blocked,
        "parked": proposals + blocked,
        "executed": [],
        "assumptions": list(seek_out.get("assumptions") or []),
        "audit": seek_out.get("audit"),
        "requires_confirm": True,
        "autonomy_level": seek_out.get("autonomy_level") or "draft_only",
        "values": [],
        **_honesty(),
    }


def _seek_ok(seek_out: dict[str, Any]) -> dict[str, Any]:
    proposals = list(seek_out.get("proposals") or [])
    blocked = list(seek_out.get("blocked") or [])
    n = len(proposals)
    titles = "; ".join(str(p.get("title") or "") for p in proposals[:5])
    answer = (
        f"SEEK {n} governed next step{'s' if n != 1 else ''}"
        + (f": {titles}" if titles else ".")
        + " Propose-only. Nothing executed."
    )
    return {
        "ok": True,
        "status": "SEEK",
        "reason": None,
        "answer": answer,
        "initiative": seek_out.get("initiative") or "proactive",
        "trigger": seek_out.get("trigger") or "liberty",
        "goal_id": seek_out.get("goal_id"),
        "goal_statement": seek_out.get("goal_statement"),
        "goal_family": seek_out.get("goal_family"),
        "proposals": proposals,
        "blocked": blocked,
        "parked": blocked,
        "executed": [],
        "assumptions": list(seek_out.get("assumptions") or []),
        "audit": seek_out.get("audit"),
        "requires_confirm": bool(seek_out.get("requires_confirm")),
        "autonomy_level": seek_out.get("autonomy_level") or "draft_only",
        "values": [],
        **_honesty(),
    }


def _resolve_goal(
    goal_id: str | None, statement: str
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """Return (goal, refuse_envelope)."""
    gid = (goal_id or "").strip() or None
    text = (statement or "").strip()
    if gid:
        goal = enterprise_goal.get_goal(gid)
        if goal is None:
            return None, _refuse(
                "unknown_goal",
                answer="REFUSE: no goal with that id is bound. Liberty seek did not invent one.",
            )
        return goal, None
    if text:
        created = enterprise_goal.create_goal(
            text,
            soft_preferences={"autonomy_level": "draft_only"},
        )
        if not created.get("ok") or not created.get("goal"):
            return None, _refuse(
                str(created.get("error") or "goal_bind_failed"),
                answer="REFUSE: could not bind a goal, so seek did not run.",
                extra={"audit": created.get("audit")},
            )
        return created["goal"], None
    goal = enterprise_goal.active_goal()
    if goal is None:
        return None, _refuse(
            "no_goal_bound",
            answer=(
                "REFUSE: no bound enterprise goal. Start liberty seek with a "
                "statement, or bind a goal first. Did not invent autonomy."
            ),
        )
    return goal, None


def start_seek(
    *,
    goal_id: str | None = None,
    statement: str = "",
    trigger: str = "liberty",
    execute: bool = False,
    limit: int = seeker.MAX_PROPOSALS,
) -> dict[str, Any]:
    """Operator start. Runs G2.1 seeker. execute=true parks; never auto-runs."""
    from CortexOS.packaging import FeatureNotInstalled, require_extra

    trig = (trigger or "liberty").strip() or "liberty"
    cap = max(1, min(int(limit or seeker.MAX_PROPOSALS), seeker.MAX_PROPOSALS))
    try:
        require_extra("agentic", feature="seeker")
        goal, refused = _resolve_goal(goal_id, statement)
        if refused is not None:
            return refused
        assert goal is not None
        out = seeker.seek(str(goal["id"]), trigger=trig, limit=cap)
    except FeatureNotInstalled as exc:
        return _refuse(
            "extra_missing",
            answer=f"REFUSE: seeker extra is not installed ({exc.extra}). Did not invent autonomy.",
        )
    except Exception as exc:
        return _refuse(
            "seek_failed",
            answer=(
                f"REFUSE: G2.1 seeker did not run ({type(exc).__name__}: {exc}). "
                "Did not invent proposals or live-host green."
            ),
        )

    if not out.get("ok"):
        return _refuse(
            str(out.get("error") or "no_goal_bound"),
            answer="REFUSE: seeker returned no bound goal. Did not invent autonomy.",
            extra={"audit": out.get("audit")},
        )

    if execute:
        return _park(
            out,
            reason="execute_parked",
            answer=(
                "PARK: liberty seek proposed next steps and parked them. "
                "Buyer surface does not auto-run, send, buy, deploy or approve. "
                "executed=[]."
            ),
        )
    audit = out.get("audit")
    if not (isinstance(audit, dict) and audit.get("ok")):
        return _park(
            out,
            reason="audit_unavailable",
            answer=(
                "PARK: seeker proposed next steps but the F1 ledger write failed. "
                "Did not invent an audit trail or execute them."
            ),
        )
    return _seek_ok(out)


def render_tool_text(envelope: dict[str, Any]) -> str:
    """Operator-visible tool payload. Tests assert on this text, not SQL only."""
    status = envelope.get("status") or "REFUSE"
    proposals = envelope.get("proposals") or []
    parked = envelope.get("parked") or []
    executed = envelope.get("executed") or []
    blocked = envelope.get("blocked") or []
    audit = envelope.get("audit") or {}
    titles = "; ".join(str(p.get("title") or "") for p in proposals[:6]) or "none"
    lines = [
        f"status: {status}",
        f"answer: {envelope.get('answer') or ''}",
        f"reason: {envelope.get('reason') or 'none'}",
        f"goal_id: {envelope.get('goal_id') or 'none'}",
        f"proposals: {titles}",
        f"proposal_count: {len(proposals)}",
        f"blocked_count: {len(blocked)}",
        f"parked_count: {len(parked)}",
        f"executed_count: {len(executed)}",
        f"audit_ok: {audit.get('ok') if isinstance(audit, dict) else False}",
        f"audit_event: {audit.get('event') if isinstance(audit, dict) else 'none'}",
        "jepa: proxy (not trained)",
        f"complete: {bool(envelope.get('complete'))}",
        f"live_5000_ci: {bool(envelope.get('live_5000_ci'))}",
        f"live_8020_ci: {bool(envelope.get('live_8020_ci'))}",
    ]
    for row in envelope.get("assumptions") or []:
        lines.append(f"assumption: {row}")
    return "\n".join(lines)
