"""G2.1 — the proactive seeker: what moves the goal when nobody asked anything.

The litmus test for this module is silence. With an empty inbox, no due
routine and no event, `seek()` must still return at least one safe, useful,
goal-relevant next step. If it can only answer when spoken to, it is not the
active engine.

Where the work comes from when nothing arrives:

* **the goal itself** — every measurable criterion that has no fresh evidence
  is an open question the engine can go and answer;
* **open loops in the engine's own state** — a routine the governor paused, an
  app stuck waiting for a look, a routine failing repeatedly;
* **what it has learned** — a task family with a proven winner that isn't
  scheduled yet is a candidate routine;
* **gaps in the goal** — a criterion with no target or no evidence source can't
  ever be judged, so closing that gap is real work.

Proposals are ranked by closeness to the goal using the same embedding the
racing scoreboard uses, and every one is passed through the ethical gate. The
seeker proposes; it does not send, publish, buy, deploy or approve. Anything
external, irreversible or money-adjacent comes back `requires_confirm`.
"""

from __future__ import annotations

import hashlib
from typing import Any

from CortexOS.execution import action_value, enterprise_goal, goal_audit
from CortexOS.memory.store import cosine

MAX_PROPOSALS = 8


def _proposal_id(goal_id: str, title: str) -> str:
    digest = hashlib.sha256(f"{goal_id}|{title}".encode("utf-8")).hexdigest()[:10]
    return f"prop-{digest}"


def _section(fn) -> Any:
    """One unavailable store must never stop the engine from thinking."""
    try:
        return fn()
    except Exception:
        return None


def gather_state() -> dict[str, Any]:
    """A read-only snapshot of what the engine already knows about itself."""

    def _routines() -> list[dict[str, Any]]:
        from CortexOS.execution import routine_scheduler

        routine_scheduler.init()
        return routine_scheduler.list_routines()

    def _apps() -> list[dict[str, Any]]:
        from CortexOS.execution import app_store

        app_store.init()
        return app_store.list_apps()

    def _families() -> list[dict[str, Any]]:
        from CortexOS.execution import scoreboard

        scoreboard.init()
        return scoreboard.list_families()

    return {
        "routines": _section(_routines) or [],
        "apps": _section(_apps) or [],
        "families": _section(_families) or [],
    }


def _goal_text(goal: dict[str, Any]) -> str:
    parts = [str(goal.get("statement") or "")]
    for criterion in goal.get("measurable_criteria") or []:
        parts.append(f"{criterion.get('name')} {criterion.get('metric')}")
    return " ".join(parts)


def _relevance(text: str, goal_vector: list[float]) -> float:
    try:
        from CortexOS.execution import scoreboard

        return round(max(0.0, cosine(scoreboard.embed_goal(text), goal_vector)), 6)
    except Exception:
        return 0.0


def _candidates(goal: dict[str, Any], state: dict[str, Any]) -> list[dict[str, Any]]:
    """Generate before ranking — deterministic, no model call, no tokens."""
    out: list[dict[str, Any]] = []
    criteria = goal.get("measurable_criteria") or []

    # 1. The goal's own criteria are standing questions.
    for criterion in criteria:
        name = criterion.get("name") or criterion.get("metric")
        source = criterion.get("evidence_source")
        if source:
            out.append(
                {
                    "title": f"Check {name} against its target",
                    "why": f"This goal is judged on {name}, and nothing has checked it recently.",
                    "action": "check_metric",
                    "source": "criterion",
                    "next_step": {"metric": criterion.get("metric"), "evidence_source": source},
                }
            )
        else:
            out.append(
                {
                    "title": f"Decide where {name} is measured from",
                    "why": f"{name} has no source of evidence, so nobody can tell if this goal is being met.",
                    "action": "propose",
                    "source": "coverage",
                    "next_step": {"metric": criterion.get("metric"), "needs": "evidence_source"},
                }
            )
        if criterion.get("target") is None and criterion.get("floor") is None:
            out.append(
                {
                    "title": f"Set a target for {name}",
                    "why": f"{name} has no number to hit, so progress can't be judged.",
                    "action": "propose",
                    "source": "coverage",
                    "next_step": {"metric": criterion.get("metric"), "needs": "target"},
                }
            )

    # 2. Open loops the engine created and never closed.
    for routine in state.get("routines") or []:
        reason = str(routine.get("paused_reason") or "")
        if routine.get("status") == "paused" and reason.startswith("governor:"):
            out.append(
                {
                    "title": f"Look at why '{routine.get('name')}' keeps failing",
                    "why": "The engine paused this routine by itself, so whatever it did for you has stopped.",
                    "action": "inspect",
                    "source": "open_loop",
                    "next_step": {"routine_id": routine.get("id")},
                }
            )
        elif int(routine.get("error_streak") or 0) > 0:
            out.append(
                {
                    "title": f"Fix the last failure in '{routine.get('name')}'",
                    "why": "Its most recent run didn't succeed, and it will pause itself if that continues.",
                    "action": "inspect",
                    "source": "open_loop",
                    "next_step": {"routine_id": routine.get("id")},
                }
            )

    for record in state.get("apps") or []:
        if record.get("status") == "draft":
            out.append(
                {
                    "title": f"Review '{record.get('name')}' — it's waiting on you",
                    "why": "This app has passed its checks and only needs your approval to be usable.",
                    "action": "propose",  # proposing a review is safe; approving is not
                    "source": "open_loop",
                    "next_step": {"app_id": record.get("id"), "human_step": "approve"},
                }
            )
        elif record.get("status") == "blocked":
            out.append(
                {
                    "title": f"Clear what's blocking '{record.get('name')}'",
                    "why": "This app can't be used until the problems found during its check are fixed.",
                    "action": "inspect",
                    "source": "open_loop",
                    "next_step": {"app_id": record.get("id")},
                }
            )

    # 3. What it has learned but never scheduled.
    scheduled = {str(r.get("prompt") or "").lower() for r in state.get("routines") or []}
    for family in (state.get("families") or [])[:5]:
        label = str(family.get("family") or "")
        pretty = label.rsplit("-", 1)[0].replace("-", " ").strip()
        if pretty and pretty.lower() not in scheduled:
            out.append(
                {
                    "title": f"Make '{pretty}' a routine",
                    "why": "The engine has done this kind of work before and knows which approach wins.",
                    "action": "draft_routine",
                    "source": "pattern",
                    "next_step": {"routine_goal": pretty},
                }
            )

    # 4. G2.5 — things the user said they'd do and never closed. These carry
    # provenance so a reminder can always be traced back to their own words.
    try:
        from CortexOS.execution import commitments

        out.extend(commitments.as_proposals(limit=3))
    except Exception:
        pass  # forget-recovery is additive; it must never break a seek

    # 5. The floor: a goal with nothing attached still has one honest next step.
    if not criteria:
        out.append(
            {
                "title": "Add something measurable to this goal",
                "why": "Right now this goal is a sentence with no way to tell whether it's being met.",
                "action": "propose",
                "source": "coverage",
                "next_step": {"needs": "measurable_criteria"},
            }
        )
    out.append(
        {
            "title": "Summarise what changed against this goal recently",
            "why": "A short written check keeps the goal honest even on a quiet day.",
            "action": "summarize",
            "source": "criterion",
            "next_step": {"window": "recent"},
        }
    )
    return out


def seek(
    goal_id: str | None = None, *, trigger: str = "manual", limit: int = MAX_PROPOSALS
) -> dict[str, Any]:
    """Advance the goal without being asked. Safe by construction."""
    from CortexOS.packaging import require_extra

    require_extra("agentic", feature="seeker")
    enterprise_goal.init()
    goal = enterprise_goal.get_goal(goal_id) if goal_id else enterprise_goal.active_goal()
    if goal is None:
        return {
            "ok": False,
            "error": "no_goal_bound",
            "initiative": "proactive",
            "proposals": [],
            "assumptions": [],
        }

    state = gather_state()
    goal_vector = []
    try:
        from CortexOS.execution import scoreboard

        goal_vector = scoreboard.embed_goal(_goal_text(goal))
    except Exception:
        goal_vector = []

    family = action_value.goal_family(goal)
    seen: set[str] = set()
    proposals: list[dict[str, Any]] = []
    blocked: list[dict[str, Any]] = []

    for candidate in _candidates(goal, state):
        title = str(candidate["title"])
        if title in seen:
            continue
        seen.add(title)

        verdict = enterprise_goal.gate_action(
            goal, action_kind=str(candidate["action"]), violates=candidate.get("violates")
        )
        relevance = (
            _relevance(f"{title} {candidate['why']}", goal_vector) if goal_vector else 0.0
        )
        # Cosine is the prior; observed outcomes shrink the estimate away from it.
        estimate = action_value.value(
            family, str(candidate["action"]), str(candidate["source"]), prior=relevance
        )
        entry = {
            "id": _proposal_id(goal["id"], title),
            "title": title,
            "why": candidate["why"],
            "action": candidate["action"],
            "source": candidate["source"],
            "risk": verdict["risk"],
            "requires_confirm": bool(verdict["requires_confirm"]),
            "auto_ok": bool(verdict["allowed"]),
            "relevance": relevance,
            "value": estimate["value"],
            "value_learned": estimate["learned"],
            "value_n": estimate["n"],
            "value_why": action_value.explain(estimate),
            "next_step": candidate.get("next_step") or {},
        }
        if verdict["blocked_by"]:
            entry["blocked_by"] = verdict["blocked_by"]
            entry["reasons"] = verdict["reasons"]
            blocked.append(entry)
            goal_audit.gate_denied(goal["id"], str(candidate["action"]), verdict)
            continue
        proposals.append(entry)

    # Value first, cosine as tie-break — identical ordering while cold, because
    # an unlearned value *is* the cosine prior.
    proposals.sort(key=lambda p: (-p["value"], -p["relevance"], p["title"]))
    proposals = proposals[:limit]

    autonomy = str(goal.get("soft_preferences", {}).get("autonomy_level") or "draft_only")
    assumptions = [
        f"Nobody asked for this — I looked at the goal \"{goal['statement']}\" and worked out what to do next.",
        f"I found {len(proposals)} next step{'s' if len(proposals) != 1 else ''}, "
        "with the most relevant first.",
    ]
    if autonomy == "draft_only":
        assumptions.append(
            "This goal is set to draft only, so I've written these up rather than doing any of them."
        )
    else:
        auto = sum(1 for p in proposals if p["auto_ok"])
        assumptions.append(
            f"{auto} of these are low-risk enough for me to do on my own; the rest wait for you."
        )
    assumptions.append(
        "I never send messages, approve apps, spend money or change anything outside this "
        "machine without you saying so."
    )

    enterprise_goal.record_seek(goal["id"], proposals, assumptions, trigger=trigger)
    audit = goal_audit.seek_recorded(goal["id"], proposals, trigger=trigger, blocked=blocked)

    return {
        "ok": True,
        "initiative": "proactive",
        "trigger": trigger,
        "goal_id": goal["id"],
        "goal_statement": goal["statement"],
        "goal_family": family,
        "proposals": proposals,
        "blocked": blocked,
        "assumptions": assumptions,
        "requires_confirm": any(p["requires_confirm"] for p in proposals),
        "autonomy_level": autonomy,
        "audit": audit,
    }


def record_proposal_outcome(
    goal_id: str, proposal_id: str, outcome: str, *, action: str = "", source: str = ""
) -> dict[str, Any]:
    """Close the loop: what the user did with a proposal teaches the value table.

    Action/source are looked up from the stored seek when not supplied, so a UI
    only has to send the proposal id and what happened to it.
    """
    goal = enterprise_goal.get_goal(goal_id)
    if goal is None:
        return {"ok": False, "error": "unknown_goal"}

    if not action or not source:
        for seek_row in enterprise_goal.list_seeks(goal_id, limit=20):
            for proposal in seek_row.get("proposals") or []:
                if proposal.get("id") == proposal_id:
                    action = action or str(proposal.get("action") or "")
                    source = source or str(proposal.get("source") or "")
                    break
            if action and source:
                break
    if not action or not source:
        return {"ok": False, "error": "unknown_proposal"}

    return action_value.record_outcome(
        action_value.goal_family(goal), action, source, outcome, proposal_id=proposal_id
    )


def seek_if_idle(goal_id: str | None = None, *, now: float | None = None) -> dict[str, Any] | None:
    """Spend the tick's spare capacity on the goal — the always-on half.

    Returns None when real scheduled work is due; the seeker never competes with
    a routine the user actually asked for.
    """
    import time as _time

    from CortexOS.execution import routine_scheduler

    routine_scheduler.init()
    moment = now if now is not None else _time.time()

    if routine_scheduler.global_budget_state()["exhausted"]:
        return None
    for routine in routine_scheduler.list_routines():
        if not routine.get("enabled") or routine.get("status") in ("paused", "running"):
            continue
        if float(routine.get("next_run_at") or 0) <= moment:
            return None  # scheduled work wins; seek on the next quiet tick

    return seek(goal_id, trigger="idle")
