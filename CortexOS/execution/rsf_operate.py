"""RSF-07 Audit/Operate stage-trace. Consume RSF-04 runs. Never invent CERTIFIED.

Audit shows per-stage CERTIFIED|ABSTAIN|REFUSE. Operate shows chosen_option
plus the route decision trace. A later CERTIFIED after a gap is displayed as
ABSTAIN so the panel cannot paint fake green.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from CortexOS.execution.rsf_orchestrator import BAKEOFF_SURFACES, bakeoff_hooks
from CortexOS.rsf import (
    RSF_STAGES,
    RSF_STATUSES,
    RsfConsumerError,
    parse_rsf_artifact,
    parse_rsf_trace,
)

_GAP_REASON = "prior stage not CERTIFIED; will not display invented CERTIFIED"
_INVALID_REASON = "invalid artifact; will not invent CERTIFIED"


def _str_list(value: Any) -> list[str]:
    if not isinstance(value, (list, tuple)):
        return []
    return [x for x in value if isinstance(x, str)]


def _route_trace(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        return []
    out: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, Mapping):
            continue
        step = str(item.get("step") or "").strip()
        if not step:
            continue
        chosen = item.get("chosen")
        if chosen is not None and not isinstance(chosen, str):
            chosen = None
        rejected = item.get("rejected") if isinstance(item.get("rejected"), Mapping) else {}
        note = item.get("note")
        out.append(
            {
                "step": step,
                "considered": _str_list(item.get("considered")),
                "chosen": chosen,
                "rejected": {str(k): str(v) for k, v in rejected.items()},
                "note": note if isinstance(note, str) else "",
            }
        )
    return out


def honest_stage_views(artifacts: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Per-stage display rows. CERTIFIED is shown only when priors allow it."""
    by_stage: dict[str, Mapping[str, Any]] = {}
    for item in artifacts:
        if not isinstance(item, Mapping):
            continue
        stage = str(item.get("stage") or "")
        if stage in RSF_STAGES:
            by_stage[stage] = item

    blocked = False
    views: list[dict[str, Any]] = []
    for stage in RSF_STAGES:
        art = by_stage.get(stage)
        if art is None:
            views.append(
                {
                    "stage": stage,
                    "status": "ABSTAIN",
                    "chosen_option": None,
                    "options": [],
                    "evidence": [],
                    "reasons": ["missing stage; will not invent CERTIFIED"],
                    "route_trace": [],
                    "artifact_id": None,
                    "raw_status": None,
                    "displayed_certified": False,
                }
            )
            blocked = True
            continue

        reasons = _str_list(art.get("reasons"))
        try:
            parsed = parse_rsf_artifact(art)
            raw_status = parsed.status
            raw_chosen = parsed.chosen_option
            options = list(parsed.options)
            evidence = list(parsed.evidence)
        except RsfConsumerError:
            raw_status = ""
            raw_chosen = None
            options = _str_list(art.get("options"))
            evidence = _str_list(art.get("evidence"))
            if _INVALID_REASON not in reasons:
                reasons = [*reasons, _INVALID_REASON]

        shown = raw_status if raw_status in RSF_STATUSES else "ABSTAIN"
        chosen = raw_chosen if isinstance(raw_chosen, str) else None
        if blocked and shown == "CERTIFIED":
            shown = "ABSTAIN"
            chosen = None
            if _GAP_REASON not in reasons:
                reasons = [*reasons, _GAP_REASON]
        if shown != "CERTIFIED":
            chosen = None
            blocked = True

        views.append(
            {
                "stage": stage,
                "status": shown,
                "chosen_option": chosen,
                "options": options,
                "evidence": evidence,
                "reasons": reasons,
                "route_trace": _route_trace(art.get("route_trace")),
                "artifact_id": art.get("artifact_id") if isinstance(art.get("artifact_id"), str) else None,
                "raw_status": raw_status or None,
                "displayed_certified": shown == "CERTIFIED",
            }
        )
    return views


def render_audit_operate(run: Mapping[str, Any] | None) -> dict[str, Any]:
    """Payload Constructor Audit / Operate panels consume. No fake green."""
    blob: Mapping[str, Any] = run if isinstance(run, Mapping) else {}
    artifacts = blob.get("artifacts")
    rows = honest_stage_views(artifacts if isinstance(artifacts, list) else [])
    legal = False
    legal_reason = ""
    if isinstance(artifacts, list):
        try:
            parse_rsf_trace(artifacts)
            legal = True
        except RsfConsumerError as exc:
            legal_reason = str(exc)
    else:
        legal_reason = "RSF trace must be a list"

    displayed = [row["status"] for row in rows]
    ok = (
        legal
        and displayed == ["CERTIFIED", "CERTIFIED", "CERTIFIED", "CERTIFIED"]
        and blob.get("engine") == "cortex"
    )
    bakeoff = blob.get("bakeoff")
    if not isinstance(bakeoff, list):
        bakeoff = bakeoff_hooks()

    audit = {
        "kind": "rsf_stage_trace",
        "surface": "audit",
        "engine": blob.get("engine") if blob.get("engine") == "cortex" else "cortex",
        "ok": ok,
        "legal": legal,
        "legal_reason": legal_reason,
        "question": blob.get("question") if isinstance(blob.get("question"), str) else "",
        "stages": rows,
        "invented_certified": False,
    }
    operate = {
        "kind": "rsf_route_decision",
        "surface": "operate",
        "engine": "cortex",
        "ok": ok,
        "legal": legal,
        "question": audit["question"],
        "destination": blob.get("destination") if isinstance(blob.get("destination"), str) else "freeroute",
        "stages": [
            {
                "stage": row["stage"],
                "status": row["status"],
                "chosen_option": row["chosen_option"],
                "route_trace": row["route_trace"],
                "displayed_certified": row["displayed_certified"],
            }
            for row in rows
        ],
        "bakeoff": bakeoff,
        "bakeoff_surfaces": list(BAKEOFF_SURFACES),
        "invented_certified": False,
    }
    return {"audit": audit, "operate": operate}


__all__ = ["honest_stage_views", "render_audit_operate"]
