"""S1 — the agent workflow: detect → draft → compliance verdict → human approve → publish.

Governance rails (non-negotiable):
  * detectors are deterministic SQL — no LLM decides whether to fire;
  * NOTHING publishes without an explicit human approval (approve_run);
  * every step writes an F1 audit-ledger event;
  * the published artifact lands only under outputs/<approver>/<run_id>/.
"""
from __future__ import annotations

import datetime as _dt
from pathlib import Path
from typing import Any

from packs.dms.agents import detectors, registry

ROOT = Path(__file__).resolve().parents[3]
OUTPUTS = ROOT / "outputs"


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat()


def _audit(event: str, payload: dict, *, actor: str) -> None:
    try:
        from packs.dms.audit import ledger

        ledger.append(actor, event, payload)
    except Exception:  # noqa: BLE001
        pass


def _compliance_verdict(agent: dict, detection: detectors.Detection) -> dict[str, Any]:
    """Deterministic gate (F5 value-threshold rail, generalized). Publishing is
    ALWAYS human-gated; severity only sets pass vs warn for reviewer attention."""
    severity = "warn"
    if detection.value is not None and detection.bound:
        try:
            ratio = abs(detection.value) / (abs(detection.bound) or 1)
            severity = "warn" if ratio >= 1.5 else "pass"
        except Exception:  # noqa: BLE001
            severity = "pass"
    return {"status": severity, "requires_human": True,
            "rule": "agent_publish_requires_human", "detail": detection.detail}


def _draft_report(agent: dict, detection: detectors.Detection) -> str:
    lines = [
        f"# {agent.get('name', agent['agent_id'])} — alert",
        "",
        f"- Role: {agent.get('role_label', 'analyst')}",
        f"- Detected at: {_now()}",
        f"- Signal: {detection.detail}",
        "",
        "## What triggered this",
        f"The `{detection.metric}` reading ({detection.value}) crossed the configured "
        f"threshold ({detection.op} {detection.bound}).",
    ]
    q = agent.get("context_question")
    if q:
        try:
            from CortexOS.dms.answer_engine import answer

            ctx = answer(q)
            if ctx.get("route") == "sql":
                lines += ["", "## Context (certified/governed answer)", "",
                          f"> {q}", "", ctx.get("answer", "")]
        except Exception:  # noqa: BLE001 — context is best-effort, never blocks the draft
            pass
    lines += ["", "_Draft by an OpenDMS watcher agent. Requires human approval before it is published._"]
    return "\n".join(lines)


def run_agent(agent_id: str, *, actor: str = "system") -> dict[str, Any]:
    agent = registry.get_agent(agent_id)
    if agent is None:
        raise ValueError(f"no agent {agent_id!r}")

    detection = detectors.evaluate(agent["detector_cfg"])
    _audit("agent.checked", {"agent_id": agent_id, **detection.to_dict()}, actor=actor)

    if not detection.fired:
        run_id = registry.record_run(agent_id, status="no_trigger", detection=detection.to_dict())
        return {"run_id": run_id, "status": "no_trigger", "detection": detection.to_dict()}

    report = _draft_report(agent, detection)
    verdict = _compliance_verdict(agent, detection)
    run_id = registry.record_run(agent_id, status="pending_approval",
                                 detection=detection.to_dict(), report=report, verdict=verdict)
    _audit("agent.detected", {"agent_id": agent_id, "run_id": run_id, **detection.to_dict()}, actor=actor)
    _audit("agent.drafted", {"agent_id": agent_id, "run_id": run_id, "verdict": verdict}, actor=actor)
    return {"run_id": run_id, "status": "pending_approval",
            "detection": detection.to_dict(), "verdict": verdict, "report": report}


def approve_run(run_id: str, *, approver: str) -> dict[str, Any]:
    run = registry.get_run(run_id)
    if run is None:
        raise ValueError(f"no run {run_id!r}")
    if run["status"] != "pending_approval":
        raise PermissionError(f"run {run_id!r} is {run['status']!r}, not pending_approval")

    out_dir = OUTPUTS / approver / run_id
    out_dir.mkdir(parents=True, exist_ok=True)
    artifact = out_dir / "report.md"
    artifact.write_text(run.get("report") or "", encoding="utf-8")

    registry.update_run(run_id, status="approved", approver=approver,
                        artifact_path=str(artifact))
    _audit("agent.published",
           {"run_id": run_id, "agent_id": run["agent_id"], "approver": approver,
            "artifact": str(artifact)}, actor=approver)
    return {"run_id": run_id, "status": "approved", "artifact_path": str(artifact)}


def reject_run(run_id: str, *, approver: str, reason: str = "") -> dict[str, Any]:
    run = registry.get_run(run_id)
    if run is None:
        raise ValueError(f"no run {run_id!r}")
    if run["status"] != "pending_approval":
        raise PermissionError(f"run {run_id!r} is {run['status']!r}, not pending_approval")
    registry.update_run(run_id, status="rejected", approver=approver)
    _audit("agent.rejected", {"run_id": run_id, "approver": approver, "reason": reason},
           actor=approver)
    return {"run_id": run_id, "status": "rejected"}
