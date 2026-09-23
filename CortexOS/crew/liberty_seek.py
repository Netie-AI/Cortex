"""LIBERTY-PREDICT-GOAL: plan language + proxy V(s,a,g) on Control+Crew.

Extends LIBERTY-SEEK (#223) and LIBERTY-JEPA-COLLAPSE (#224). Operator
set/predict-goal is Crew POST /crew/liberty/predict-goal. The plan is words
plus tabular/proxy action_value V(s,a,g) (G2.2). That is not a trained JEPA
forecast, not a future observation, and never WM COMPLETE. Collapse stays
proxy cosine. CoT climb leftover (#212) is cited, not replaced. No live-host
invent-green.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from CortexOS.execution import action_value, enterprise_goal, seeker
from CortexOS.execution.gen_cfsm import collapse_score
from CortexOS.execution.scoreboard import embed_goal

LAW = (
    "G2.1 governed proactive seek on Control+Crew, collapsed with G1 proxy "
    "JEPA collapse_score, then G2.2 predict-goal as plan language + tabular "
    "proxy V(s,a,g). Operator starts seek on Crew POST /crew/liberty/seek. "
    "Operator set/predict-goal is POST /crew/liberty/predict-goal. Control "
    "GET-displays this map (F-0030, no spawn). Seeker proposes; it does not "
    "send, publish, buy, deploy or approve. Gates fail closed: no bound goal "
    "REFUSE; execute/auto-run PARK with executed=[]. Candidates collapse with "
    "CortexOS.execution.gen_cfsm.collapse_score (proxy cosine). Predict-goal "
    "ranks with CortexOS.execution.action_value.value -- proxy table, not a "
    "trained world model, not a future-observation forecast, not COMPLETE. "
    "CoT climb #212 stays INCOMPLETE; DMS #180 gen 57.69% / exact 38.46% "
    "WRONG=0 is cited, not replaced. Not COMPLETE. No live :5000/:8020 CI claim."
)

STATUSES = ("SEEK", "PLAN", "PARK", "REFUSE")
ENGINE_SEEK = "CortexOS.execution.seeker.seek"
COLLAPSE_SOT = "CortexOS.execution.gen_cfsm.collapse_score"
VALUE_SOT = "CortexOS.execution.action_value.value"
EXECUTE = "POST /crew/liberty/seek"
PREDICT = "POST /crew/liberty/predict-goal"
DISPLAY = "GET /crew/liberty"
ENGINE_HTTP = "POST /api/engine/seek"
PLAN_LANGUAGE = "plan"

# Frozen DMS #180 numbers. Do not invent a better climb %.
_MEASURED_BASELINE = {
    "cite": "DMS #180 Formal GREEN @ d2f116a6",
    "gen": "57.69%",
    "exact": "38.46%",
    "wrong": 0,
    "replaces_baseline": False,
    "invented_better": False,
}

_INVENT_TRAINED_KEYS = (
    "trained",
    "world_model",
    "complete",
    "mode",
    "trained_forecast",
    "future_observation",
    "forecast",
)


def jepa_stamp(
    *,
    collapse_ok: bool | None = None,
    extra: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Honest proxy stamp. Callers cannot opt into trained WM COMPLETE."""
    stamp: dict[str, Any] = {
        "mode": "proxy",
        "trained": False,
        "world_model": False,
        "complete": False,
        "source": COLLAPSE_SOT,
        "path": "proxy cosine via gen_cfsm.collapse_score; not trained JEPA / world model",
    }
    if collapse_ok is not None:
        stamp["collapse_ok"] = bool(collapse_ok)
    if extra:
        for key, value in extra.items():
            if key in _INVENT_TRAINED_KEYS:
                continue
            stamp[key] = value
    stamp["mode"] = "proxy"
    stamp["trained"] = False
    stamp["world_model"] = False
    stamp["complete"] = False
    stamp["source"] = COLLAPSE_SOT
    return stamp


def forecast_stamp(*, extra: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Honest proxy V(s,a,g) stamp. Callers cannot opt into trained forecasts."""
    stamp: dict[str, Any] = {
        "mode": "proxy",
        "language": PLAN_LANGUAGE,
        "trained": False,
        "trained_forecast": False,
        "future_observation": False,
        "world_model": False,
        "complete": False,
        "source": VALUE_SOT,
        "path": "tabular/proxy V(s,a,g); plan language, not a trained JEPA forecast",
    }
    if extra:
        for key, value in extra.items():
            if key in _INVENT_TRAINED_KEYS:
                continue
            stamp[key] = value
    stamp["mode"] = "proxy"
    stamp["language"] = PLAN_LANGUAGE
    stamp["trained"] = False
    stamp["trained_forecast"] = False
    stamp["future_observation"] = False
    stamp["world_model"] = False
    stamp["complete"] = False
    stamp["source"] = VALUE_SOT
    return stamp


def _collapse_meta(*, ok: bool, count: int = 0, reason: str | None = None) -> dict[str, Any]:
    body: dict[str, Any] = {
        "ok": bool(ok),
        "sot": COLLAPSE_SOT,
        "mode": "proxy",
        "trained": False,
        "world_model": False,
        "complete": False,
        "count": int(count),
    }
    if reason:
        body["reason"] = reason
    return body


def _honesty() -> dict[str, Any]:
    return {
        "complete": False,
        "issue_223_complete": False,
        "issue_222_complete": False,
        "issue_212_complete": False,
        "issue_224": False,
        "issue_224_complete": False,
        "issue_225": False,
        "issue_225_complete": False,
        "jepa": jepa_stamp(),
        "collapse": _collapse_meta(ok=False, reason="not_run"),
        "forecast": forecast_stamp(),
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
        "collapse_sot": COLLAPSE_SOT,
        "value_sot": VALUE_SOT,
        "predict": PREDICT,
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
        "predict": PREDICT,
        "display": DISPLAY,
        "engine_http": ENGINE_HTTP,
        "statuses": list(STATUSES),
        "initiative": "proactive",
        "propose_only": True,
        "agents": (
            "Start liberty seek on Crew. Predict goal uses plan language + "
            "proxy V(s,a,g). Control is GET-display only. Proposals are ranked "
            "next steps collapsed with proxy JEPA collapse_score, then scored "
            "with tabular action_value -- not executed autonomy, not a trained "
            "world model, not a trained forecast. No bound goal REFUSE. "
            "execute=true PARK (executed stays empty)."
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
            "jepa": jepa_stamp(collapse_ok=False),
        }
    rows = enterprise_goal.list_seeks(str(goal["id"]), limit=limit)
    return {
        "ok": True,
        "goal_id": goal["id"],
        "seeks": rows,
        "live_5000_ci": False,
        "live_8020_ci": False,
        "jepa": jepa_stamp(),
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


def collapse_candidates(seek_out: Mapping[str, Any]) -> dict[str, Any]:
    """Rank liberty candidates with G1 collapse_score. Proxy only; never trained WM."""
    out = dict(seek_out)
    goal_text = str(out.get("goal_statement") or "")
    collapse_ok = True
    goal_vec: list[float] = []
    reason: str | None = None
    try:
        if not goal_text.strip():
            raise ValueError("no_goal_text")
        goal_vec = embed_goal(goal_text)
    except Exception:
        collapse_ok = False
        reason = "collapse_unavailable"

    def _one(row: Mapping[str, Any]) -> dict[str, Any]:
        item = dict(row)
        score = None
        if collapse_ok:
            blob = f"{item.get('title') or ''} {item.get('why') or ''}"
            try:
                score = round(float(collapse_score(embed_goal(blob), goal_vec)), 6)
            except Exception:
                score = None
        item["collapse_score"] = score
        item["jepa"] = "proxy"
        item["jepa_trained"] = False
        return item

    proposals = [_one(p) for p in (out.get("proposals") or [])]
    blocked = [_one(p) for p in (out.get("blocked") or [])]
    if collapse_ok:
        if any(p.get("collapse_score") is None for p in proposals + blocked):
            collapse_ok = False
            reason = "collapse_unavailable"
        else:
            proposals.sort(
                key=lambda p: (
                    -(p["collapse_score"] if p["collapse_score"] is not None else -1.0),
                    str(p.get("title") or ""),
                )
            )
            blocked.sort(
                key=lambda p: (
                    -(p["collapse_score"] if p["collapse_score"] is not None else -1.0),
                    str(p.get("title") or ""),
                )
            )
            reason = None

    assumptions = list(out.get("assumptions") or [])
    assumptions.append(
        "Candidates were collapsed with a proxy JEPA collapse score, not a trained world model."
    )
    out["proposals"] = proposals
    out["blocked"] = blocked
    out["assumptions"] = assumptions
    out["collapse"] = _collapse_meta(ok=collapse_ok, count=len(proposals), reason=reason)
    out["jepa"] = jepa_stamp(collapse_ok=collapse_ok)
    return out


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
        "plan": None,
        **_honesty(),
        "jepa": jepa_stamp(collapse_ok=False),
        "collapse": _collapse_meta(ok=False, count=0, reason="no_candidates"),
        "forecast": forecast_stamp(),
    }
    if extra:
        body.update(extra)
        body["jepa"] = jepa_stamp(collapse_ok=False)
        body["complete"] = False
    return body


def _park(
    seek_out: dict[str, Any],
    *,
    reason: str,
    answer: str,
) -> dict[str, Any]:
    proposals = list(seek_out.get("proposals") or [])
    blocked = list(seek_out.get("blocked") or [])
    collapse = seek_out.get("collapse") or _collapse_meta(ok=False, count=len(proposals))
    plan = seek_out.get("plan")
    values = list(seek_out.get("values") or [])
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
        "values": values,
        "plan": plan,
        **_honesty(),
        "jepa": jepa_stamp(collapse_ok=bool(collapse.get("ok"))),
        "collapse": collapse,
        "forecast": seek_out.get("forecast") or forecast_stamp(),
    }


def _seek_ok(seek_out: dict[str, Any]) -> dict[str, Any]:
    proposals = list(seek_out.get("proposals") or [])
    blocked = list(seek_out.get("blocked") or [])
    collapse = seek_out.get("collapse") or _collapse_meta(ok=False, count=len(proposals))
    n = len(proposals)
    titles = "; ".join(str(p.get("title") or "") for p in proposals[:5])
    answer = (
        f"SEEK {n} governed next step{'s' if n != 1 else ''}"
        + (f": {titles}" if titles else ".")
        + " Propose-only. Nothing executed. Proxy JEPA collapse_score; not a trained world model."
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
        "values": list(seek_out.get("values") or []),
        "plan": seek_out.get("plan"),
        **_honesty(),
        "jepa": jepa_stamp(collapse_ok=bool(collapse.get("ok"))),
        "collapse": collapse,
        "forecast": seek_out.get("forecast") or forecast_stamp(),
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


def _run_seeker(
    *,
    goal_id: str | None,
    statement: str,
    trigger: str,
    limit: int,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """Shared G2.1 seek + proxy collapse. Returns (seek_out, refuse)."""
    from CortexOS.packaging import FeatureNotInstalled, require_extra

    trig = (trigger or "liberty").strip() or "liberty"
    cap = max(1, min(int(limit or seeker.MAX_PROPOSALS), seeker.MAX_PROPOSALS))
    try:
        require_extra("agentic", feature="seeker")
        goal, refused = _resolve_goal(goal_id, statement)
        if refused is not None:
            return None, refused
        assert goal is not None
        out = seeker.seek(str(goal["id"]), trigger=trig, limit=cap)
    except FeatureNotInstalled as exc:
        return None, _refuse(
            "extra_missing",
            answer=f"REFUSE: seeker extra is not installed ({exc.extra}). Did not invent autonomy.",
        )
    except Exception as exc:
        return None, _refuse(
            "seek_failed",
            answer=(
                f"REFUSE: G2.1 seeker did not run ({type(exc).__name__}: {exc}). "
                "Did not invent proposals or live-host green."
            ),
        )

    if not out.get("ok"):
        return None, _refuse(
            str(out.get("error") or "no_goal_bound"),
            answer="REFUSE: seeker returned no bound goal. Did not invent autonomy.",
            extra={"audit": out.get("audit")},
        )
    return collapse_candidates(out), None


def start_seek(
    *,
    goal_id: str | None = None,
    statement: str = "",
    trigger: str = "liberty",
    execute: bool = False,
    limit: int = seeker.MAX_PROPOSALS,
) -> dict[str, Any]:
    """Operator start. Runs G2.1 seeker, then proxy-collapses candidates."""
    out, refused = _run_seeker(
        goal_id=goal_id,
        statement=statement,
        trigger=trigger,
        limit=limit,
    )
    if refused is not None:
        return refused
    assert out is not None

    if execute:
        return _park(
            out,
            reason="execute_parked",
            answer=(
                "PARK: liberty seek proposed next steps, collapsed them with "
                "proxy JEPA collapse_score, and parked them. Buyer surface does "
                "not auto-run, send, buy, deploy or approve. executed=[]. "
                "Not a trained world model."
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


def _value_sort_key(row: Mapping[str, Any]) -> tuple[float, float, str]:
    raw_v = row.get("value")
    raw_c = row.get("collapse_score")
    value = float(raw_v) if isinstance(raw_v, (int, float)) else -1.0
    collapse = float(raw_c) if isinstance(raw_c, (int, float)) else -1.0
    return (-value, -collapse, str(row.get("title") or ""))


def tabular_values(seek_out: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Proxy V(s,a,g) rows. Empty on miss; never invents a trained forecast."""
    family = str(seek_out.get("goal_family") or "")
    goal_id = str(seek_out.get("goal_id") or "")
    rows: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()

    def _add(
        action: str,
        source: str,
        *,
        prior: float = 0.0,
        why: str = "",
        fallback: float | None = None,
    ) -> None:
        key = (family, action, source)
        if not action or key in seen:
            return
        seen.add(key)
        try:
            estimate = action_value.value(family, action, source, prior=float(prior or 0.0))
            value = estimate.get("value")
            n = estimate.get("n")
            learned = bool(estimate.get("learned"))
            prior_v = estimate.get("prior")
            why_text = why or action_value.explain(estimate)
            ok = True
        except Exception:
            value = fallback
            n = 0
            learned = False
            prior_v = prior
            why_text = why or (
                "proxy V unavailable; did not invent a trained forecast"
            )
            ok = False
        rows.append(
            {
                "s": family,
                "a": action,
                "g": goal_id,
                "source": source,
                "value": value,
                "n": n,
                "learned": learned,
                "prior": prior_v,
                "value_why": why_text,
                "ok": ok,
                "mode": "proxy",
                "trained": False,
                "forecast": False,
                "sot": VALUE_SOT,
            }
        )

    for item in list(seek_out.get("proposals") or []) + list(seek_out.get("blocked") or []):
        prior = item.get("relevance")
        try:
            prior_f = float(prior) if prior is not None else 0.0
        except (TypeError, ValueError):
            prior_f = 0.0
        fallback = item.get("value")
        fallback_f = float(fallback) if isinstance(fallback, (int, float)) else None
        _add(
            str(item.get("action") or ""),
            str(item.get("source") or ""),
            prior=prior_f,
            why=str(item.get("value_why") or ""),
            fallback=fallback_f,
        )
    try:
        if family:
            for item in action_value.table(family):
                _add(str(item.get("action_kind") or ""), str(item.get("source") or ""))
    except Exception:
        pass
    return rows


def plan_language(
    seek_out: Mapping[str, Any], values: list[Mapping[str, Any]]
) -> dict[str, Any]:
    """Plan-language next steps scored by proxy V(s,a,g). Not a forecast."""
    proposals = sorted(list(seek_out.get("proposals") or []), key=_value_sort_key)
    goal = str(seek_out.get("goal_statement") or "")
    family = str(seek_out.get("goal_family") or "")
    goal_id = str(seek_out.get("goal_id") or "")
    by_key = {(str(v.get("a")), str(v.get("source"))): v for v in values}
    steps: list[dict[str, Any]] = []
    lines: list[str] = []
    for index, item in enumerate(proposals, 1):
        action = str(item.get("action") or "")
        source = str(item.get("source") or "")
        row = by_key.get((action, source), {})
        raw_v = row.get("value", item.get("value"))
        value = float(raw_v) if isinstance(raw_v, (int, float)) else None
        value_text = "unavailable" if value is None else str(value)
        title = str(item.get("title") or "")
        why = str(item.get("why") or "")
        value_why = str(row.get("value_why") or item.get("value_why") or "proxy table")
        line = (
            f"{index}. {title} -- {why} V(s,a,g)={value_text} ({value_why}). "
            "Plan language; not a trained JEPA forecast."
        )
        steps.append(
            {
                "step": index,
                "title": title,
                "why": why,
                "action": action,
                "source": source,
                "s": family,
                "a": action,
                "g": goal_id,
                "value": value,
                "value_why": value_why,
                "collapse_score": item.get("collapse_score"),
                "language": PLAN_LANGUAGE,
                "trained_forecast": False,
                "future_observation": False,
                "line": line,
            }
        )
        lines.append(line)
    n = len(steps)
    summary = (
        f"PLAN {n} proxy V(s,a,g) next step{'s' if n != 1 else ''} toward "
        f'"{goal}". Plan language + tabular action_value. '
        "Not a trained JEPA forecast and not a future observation."
    )
    return {
        "kind": "plan",
        "language": PLAN_LANGUAGE,
        "summary": summary,
        "text": summary + (("\n" + "\n".join(lines)) if lines else ""),
        "steps": steps,
        "trained_forecast": False,
        "future_observation": False,
        "world_model": False,
        "complete": False,
        "sot": VALUE_SOT,
        "mode": "proxy",
    }


def attach_predict(seek_out: Mapping[str, Any]) -> dict[str, Any]:
    """Rank by proxy V(s,a,g) and attach plan language. Never trained forecasts."""
    out = dict(seek_out)
    proposals = sorted(list(out.get("proposals") or []), key=_value_sort_key)
    out["proposals"] = proposals
    values = tabular_values(out)
    plan = plan_language(out, values)
    assumptions = list(out.get("assumptions") or [])
    assumptions.append(
        "Predict-goal used plan language and proxy tabular V(s,a,g), not a trained JEPA forecast."
    )
    out["assumptions"] = assumptions
    out["values"] = values
    out["plan"] = plan
    out["forecast"] = forecast_stamp()
    return out


def _plan_ok(seek_out: dict[str, Any]) -> dict[str, Any]:
    body = _seek_ok(seek_out)
    plan = seek_out.get("plan") or plan_language(seek_out, list(seek_out.get("values") or []))
    body["ok"] = True
    body["status"] = "PLAN"
    body["answer"] = str(plan.get("summary") or "")
    body["plan"] = plan
    body["values"] = list(seek_out.get("values") or [])
    body["forecast"] = seek_out.get("forecast") or forecast_stamp()
    return body


def _wanted_trained_forecast(
    *,
    trained: bool,
    forecast: bool,
    future_observation: bool,
) -> bool:
    return bool(trained or forecast or future_observation)


def predict_goal(
    *,
    goal_id: str | None = None,
    statement: str = "",
    trigger: str = "predict-goal",
    execute: bool = False,
    limit: int = seeker.MAX_PROPOSALS,
    trained: bool = False,
    forecast: bool = False,
    future_observation: bool = False,
) -> dict[str, Any]:
    """Operator set/predict-goal. Plan language + proxy V(s,a,g). No trained JEPA."""
    if _wanted_trained_forecast(
        trained=trained, forecast=forecast, future_observation=future_observation
    ):
        return _refuse(
            "invent_trained_forecast",
            answer=(
                "REFUSE: predict-goal is plan language + proxy V(s,a,g). "
                "Did not invent a trained JEPA forecast or future observation."
            ),
        )
    out, refused = _run_seeker(
        goal_id=goal_id,
        statement=statement,
        trigger=trigger or "predict-goal",
        limit=limit,
    )
    if refused is not None:
        return refused
    assert out is not None
    out = attach_predict(out)

    if execute:
        return _park(
            out,
            reason="execute_parked",
            answer=(
                "PARK: predict-goal wrote a plan with proxy V(s,a,g) and parked it. "
                "Buyer surface does not auto-run, send, buy, deploy or approve. "
                "executed=[]. Not a trained JEPA forecast."
            ),
        )
    audit = out.get("audit")
    if not (isinstance(audit, dict) and audit.get("ok")):
        return _park(
            out,
            reason="audit_unavailable",
            answer=(
                "PARK: predict-goal wrote a plan but the F1 ledger write failed. "
                "Did not invent an audit trail, trained forecast, or execute it."
            ),
        )
    return _plan_ok(out)


def render_tool_text(envelope: dict[str, Any]) -> str:
    """Operator-visible tool payload. Tests assert on this text, not SQL only."""
    status = envelope.get("status") or "REFUSE"
    proposals = envelope.get("proposals") or []
    parked = envelope.get("parked") or []
    executed = envelope.get("executed") or []
    blocked = envelope.get("blocked") or []
    audit = envelope.get("audit") or {}
    collapse = envelope.get("collapse") or {}
    jepa = envelope.get("jepa") or {}
    titles = "; ".join(str(p.get("title") or "") for p in proposals[:6]) or "none"
    scores = ", ".join(
        "none" if p.get("collapse_score") is None else str(p.get("collapse_score"))
        for p in proposals[:6]
    ) or "none"
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
        f"jepa_source: {jepa.get('source') or COLLAPSE_SOT}",
        f"jepa_trained: {bool(jepa.get('trained'))}",
        f"collapse_ok: {bool(collapse.get('ok'))}",
        f"collapse_sot: {collapse.get('sot') or COLLAPSE_SOT}",
        f"collapse_scores: {scores}",
        f"complete: {bool(envelope.get('complete'))}",
        f"live_5000_ci: {bool(envelope.get('live_5000_ci'))}",
        f"live_8020_ci: {bool(envelope.get('live_8020_ci'))}",
        f"plan_language: {(envelope.get('plan') or {}).get('language') or 'none'}",
        f"trained_forecast: {bool((envelope.get('forecast') or {}).get('trained_forecast'))}",
        f"future_observation: {bool((envelope.get('forecast') or {}).get('future_observation'))}",
        f"value_sot: {envelope.get('value_sot') or VALUE_SOT}",
        f"value_count: {len(envelope.get('values') or [])}",
    ]
    plan = envelope.get("plan") or {}
    if plan.get("summary"):
        lines.append(f"plan: {plan.get('summary')}")
    if plan.get("text"):
        lines.append(f"plan_text: {plan.get('text')}")
    for row in envelope.get("values") or []:
        lines.append(
            f"V(s,a,g): s={row.get('s')} a={row.get('a')} g={row.get('g')} "
            f"value={row.get('value')} trained={bool(row.get('trained'))} "
            f"forecast={bool(row.get('forecast'))}"
        )
    for row in envelope.get("assumptions") or []:
        lines.append(f"assumption: {row}")
    return "\n".join(lines)


def render_predict_text(envelope: dict[str, Any]) -> str:
    """Operator-visible predict-goal payload. Assert on this text, not SQL."""
    return render_tool_text(envelope)
