"""RSF-04 meta-router: certify research → segment → classify → filter.

AI may propose; this module certifies via RSF-02. Later stages do not run as
CERTIFIED unless every prior stage in ``RSF_STAGES`` is CERTIFIED.

RSF-01 options are advisory distill (listed, never shipped as product_engine).
Not an n8n / LangChain / LangFlow engine swap. No third orchestrator daemon.
No ``/v1/rsf`` (not a cortex-contract bump). Constructor consume is RSF-05.
Distill harness (improve→scale-check→improve) is RSF-06.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from CortexOS.execution.distill_options import option_ids
from CortexOS.execution.rsf_boundary import gate_research_egress
from CortexOS.rsf import RSF_STAGES, parse_rsf_artifact, parse_rsf_trace

# Ticket #154 lists these four names. Execution / prior-gating follows RSF_STAGES
# (research first) so traces stay legal for DMS + Constructor parseRsfTrace.
PIPELINE_STAGES = RSF_STAGES

BAKEOFF_SURFACES: tuple[str, ...] = ("dms_rag", "normal_chat", "agentic_actions")

_BANNED_ENGINE_IDS = frozenset(
    {
        "n8n",
        "myn8n",
        "langchain",
        "langchain_core",
        "langchain_community",
        "langflow",
        "langgraph",
        "crew",
        "crewai",
        "activepieces",
    }
)

ProposeFn = Callable[[str, str, Sequence[Mapping[str, Any]]], Mapping[str, Any] | None]


def is_banned_engine_id(value: str | None) -> bool:
    if not value:
        return False
    blob = value.strip().lower().replace("-", "_")
    root = blob.split(".", 1)[0]
    return blob in _BANNED_ENGINE_IDS or root in _BANNED_ENGINE_IDS


def _str_list(value: Any) -> list[str]:
    if not isinstance(value, (list, tuple)):
        return []
    return [x for x in value if isinstance(x, str)]


def _new_id(stage: str) -> str:
    return f"rsf_{stage}_{uuid.uuid4().hex[:12]}"


def _advisory_decision(*, engine_choice: str | None) -> dict[str, Any]:
    considered = ["cortex", *sorted(option_ids())]
    rejected = {
        oid: "distill_only; never product_engine"
        for oid in ("myn8n", "langchain", "langflow")
    }
    chosen = engine_choice if engine_choice in considered else None
    note = "RSF-01 options are advisory distill. Product engine stays cortex."
    if chosen == "gencfsm_dag":
        note = (
            "learn via CortexOS.execution.gen_cfsm -> dag_runner. "
            "No third orchestrator. Not n8n/LangChain/LangFlow."
        )
    return {
        "step": "meta_router",
        "considered": considered,
        "chosen": chosen,
        "rejected": rejected,
        "note": note,
    }


def _caller_decisions(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        return []
    out: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, Mapping):
            continue
        step = str(item.get("step") or "").strip()
        if not step:
            continue
        considered = _str_list(item.get("considered"))
        chosen = item.get("chosen")
        if chosen is not None and not isinstance(chosen, str):
            chosen = None
        if chosen is not None and chosen not in considered:
            considered = [*considered, chosen]
        rejected = item.get("rejected") if isinstance(item.get("rejected"), Mapping) else {}
        rejected_s = {
            str(k): str(v) for k, v in rejected.items() if str(v).strip()
        }
        note = item.get("note")
        out.append(
            {
                "step": step,
                "considered": considered,
                "chosen": chosen,
                "rejected": rejected_s,
                "note": note if isinstance(note, str) else "",
            }
        )
    return out


def _engine_choice(status: str, chosen_option: str | None) -> str | None:
    if status != "CERTIFIED":
        return None
    if chosen_option == "gencfsm_dag":
        return "gencfsm_dag"
    return "cortex"


def _emit(
    *,
    stage: str,
    question: str,
    status: str,
    options: list[str],
    chosen_option: str | None,
    evidence: list[str],
    reasons: list[str],
    extra_trace: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    route_trace = list(extra_trace or [])
    route_trace.append(_advisory_decision(engine_choice=_engine_choice(status, chosen_option)))
    wire: dict[str, Any] = {
        "artifact_id": _new_id(stage),
        "stage": stage,
        "question": question,
        "options": list(options),
        "chosen_option": chosen_option,
        "route_trace": route_trace,
        "evidence": list(evidence),
        "status": status,
        "reasons": list(reasons),
    }
    parse_rsf_artifact(wire)
    return wire


def _abstain(
    stage: str,
    question: str,
    reasons: list[str],
    *,
    options: list[str] | None = None,
    extra_trace: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return _emit(
        stage=stage,
        question=question,
        status="ABSTAIN",
        options=list(options or []),
        chosen_option=None,
        evidence=[],
        reasons=reasons,
        extra_trace=extra_trace,
    )


def _refuse(
    stage: str,
    question: str,
    reasons: list[str],
    *,
    options: list[str] | None = None,
    extra_trace: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return _emit(
        stage=stage,
        question=question,
        status="REFUSE",
        options=list(options or []),
        chosen_option=None,
        evidence=[],
        reasons=reasons,
        extra_trace=extra_trace,
    )


def _from_proposal(stage: str, question: str, proposal: Mapping[str, Any] | None) -> dict[str, Any]:
    if not isinstance(proposal, Mapping):
        return _abstain(
            stage,
            question,
            ["no proposal; will not invent a classification"],
        )
    options = _str_list(proposal.get("options"))
    evidence = _str_list(proposal.get("evidence"))
    reasons = _str_list(proposal.get("reasons"))
    extra = _caller_decisions(proposal.get("route_trace"))
    chosen = proposal.get("chosen_option")
    if chosen is not None and not isinstance(chosen, str):
        chosen = None
    if isinstance(chosen, str) and not chosen.strip():
        chosen = None

    if chosen is not None and is_banned_engine_id(chosen):
        return _refuse(
            stage,
            question,
            [
                f"BAN: {chosen} cannot be the Constructor product_engine. "
                "Distill-only analog. Engine stays cortex."
            ],
            options=options or [chosen],
            extra_trace=extra,
        )
    if not chosen or chosen not in options or not evidence:
        return _abstain(
            stage,
            question,
            reasons
            or ["insufficient evidence or choice; will not invent-green CERTIFIED"],
            options=options,
            extra_trace=extra,
        )
    return _emit(
        stage=stage,
        question=question,
        status="CERTIFIED",
        options=options,
        chosen_option=chosen,
        evidence=evidence,
        reasons=reasons or [f"{stage} certified from proposal"],
        extra_trace=extra,
    )


def bakeoff_hooks() -> list[dict[str, Any]]:
    """Instrumentation slots for Cortex meta-route vs direct analog.

    Unmeasured fields stay null. No target numbers. Analogs are not executed.
    """
    analog_ids = ["myn8n", "langchain", "langflow"]
    empty = {"latency_ms": None, "cost_myr": None, "tokens": None}
    return [
        {
            "surface": surface,
            "cortex_meta_route": dict(empty),
            "direct_option_adapter": {
                **empty,
                "option_ids": analog_ids,
                "note": "advisory distill; not executed; not product_engine",
            },
            "note": "instrumentation hooks only; no target numbers",
        }
        for surface in BAKEOFF_SURFACES
    ]


def run_rsf(
    question: str,
    *,
    proposals: Mapping[str, Any] | None = None,
    propose: ProposeFn | None = None,
    destination: str = "freeroute",
) -> dict[str, Any]:
    """Run the four RSF stages. Returns per-stage wire artifacts plus bake-off hooks."""
    started = time.perf_counter()
    q = (question or "").strip()
    proposal_map: Mapping[str, Any] = proposals or {}
    artifacts: list[dict[str, Any]] = []
    blocked = False
    blocked_reason = ""

    for stage in PIPELINE_STAGES:
        if not q:
            artifacts.append(
                _abstain(
                    stage,
                    "(missing)",
                    ["question required; nothing was chosen"],
                )
            )
            blocked = True
            blocked_reason = "question required; will not invent a downstream classification"
            continue
        if blocked:
            artifacts.append(
                _abstain(
                    stage,
                    q,
                    [blocked_reason],
                )
            )
            continue
        if stage == "research":
            gate = gate_research_egress(destination=destination)
            if gate.get("allowed") is not True:
                artifacts.append(
                    _refuse(
                        stage,
                        q,
                        list(gate.get("reasons") or ["leave-machine gate denied"]),
                    )
                )
                blocked = True
                blocked_reason = (
                    "research REFUSE; will not invent a downstream classification"
                )
                continue

        proposal: Mapping[str, Any] | None = None
        raw_prop = proposal_map.get(stage)
        if isinstance(raw_prop, Mapping):
            proposal = raw_prop
        elif propose is not None:
            proposal = propose(stage, q, tuple(artifacts))

        wire = _from_proposal(stage, q, proposal)
        artifacts.append(wire)
        if wire["status"] != "CERTIFIED":
            blocked = True
            blocked_reason = (
                f"{stage} {wire['status']}; will not invent a downstream classification"
            )

    parse_rsf_trace(artifacts)
    elapsed_ms = (time.perf_counter() - started) * 1000.0
    statuses = [a["status"] for a in artifacts]
    return {
        "ok": statuses == ["CERTIFIED", "CERTIFIED", "CERTIFIED", "CERTIFIED"],
        "engine": "cortex",
        "question": q or "(missing)",
        "artifacts": artifacts,
        "stages": [a["stage"] for a in artifacts],
        "bakeoff": bakeoff_hooks(),
        "latency_ms": elapsed_ms,
        "cost_myr": None,
        "tokens": None,
    }


__all__ = [
    "BAKEOFF_SURFACES",
    "PIPELINE_STAGES",
    "bakeoff_hooks",
    "is_banned_engine_id",
    "run_rsf",
]
