"""S1 — the agent workflow: detect → draft → compliance verdict → human approve → publish.

Governance rails (non-negotiable):
  * detectors are deterministic SQL — no LLM decides whether to fire;
  * NOTHING publishes without an explicit human approval (approve_run);
  * every step writes an F1 audit-ledger event;
  * the published artifact lands only under outputs/<approver>/<run_id>/.

Durable resume (B1):
  * ops-DB step checkpoints always (works without the ``dbos`` library);
  * when ``dbos`` is installed (``pip install -e ".[agents]"``), steps are also
    annotated as DBOS steps / workflow with SetWorkflowID idempotency;
  * Temporal is not used.
"""
from __future__ import annotations

import datetime as _dt
import json
import uuid
from pathlib import Path
from typing import Any, Callable

from packs.dms.agents import dbos_runtime, detectors, registry

ROOT = Path(__file__).resolve().parents[3]
OUTPUTS = ROOT / "outputs"

_dbos_bound_generation = -1


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


def _detection_from_dict(payload: dict[str, Any]) -> detectors.Detection:
    return detectors.Detection(
        fired=bool(payload.get("fired")),
        value=payload.get("value"),
        bound=float(payload.get("bound") or 0),
        op=str(payload.get("op") or ""),
        metric=str(payload.get("metric") or ""),
        detail=str(payload.get("detail") or ""),
    )


def _result_from_run(run: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {
        "run_id": run["run_id"],
        "status": run["status"],
        "detection": run.get("detection") or {},
        "workflow_id": run.get("workflow_id"),
        "last_step": run.get("last_step"),
    }
    if run.get("verdict"):
        out["verdict"] = run["verdict"]
    if run.get("report"):
        out["report"] = run["report"]
    if run.get("artifact_path"):
        out["artifact_path"] = run["artifact_path"]
    return out


# --- Step bodies (I/O lives here; DBOS annotates when available) ---------------

def _step_detect_impl(agent_id: str, *, actor: str, run_id: str) -> dict[str, Any]:
    prior = registry.get_step(run_id, "detect")
    if prior is not None:
        return prior["output"].get("detection") or prior["output"]

    agent = registry.get_agent(agent_id)
    if agent is None:
        raise ValueError(f"no agent {agent_id!r}")
    detection = detectors.evaluate(agent["detector_cfg"])
    payload = detection.to_dict()
    _audit("agent.checked", {"agent_id": agent_id, "run_id": run_id, **payload}, actor=actor)
    registry.checkpoint_step(run_id, "detect", {"detection": payload})
    return payload


def _step_draft_impl(agent_id: str, *, actor: str, run_id: str,
                     detection: dict[str, Any]) -> dict[str, Any]:
    prior = registry.get_step(run_id, "draft")
    if prior is not None:
        run = registry.get_run(run_id)
        assert run is not None
        return _result_from_run(run)

    agent = registry.get_agent(agent_id)
    if agent is None:
        raise ValueError(f"no agent {agent_id!r}")
    det = _detection_from_dict(detection)
    report = _draft_report(agent, det)
    verdict = _compliance_verdict(agent, det)
    registry.update_run(
        run_id,
        status="pending_approval",
        detection=json.dumps(detection),
        report=report,
        verdict=json.dumps(verdict),
    )
    registry.checkpoint_step(run_id, "draft", {"run_id": run_id, "verdict": verdict})
    _audit("agent.detected", {"agent_id": agent_id, "run_id": run_id, **detection}, actor=actor)
    _audit("agent.drafted", {"agent_id": agent_id, "run_id": run_id, "verdict": verdict},
           actor=actor)
    return {"run_id": run_id, "status": "pending_approval",
            "detection": detection, "verdict": verdict, "report": report,
            "last_step": "draft"}


def _step_record_no_trigger_impl(agent_id: str, *, run_id: str,
                                 detection: dict[str, Any]) -> dict[str, Any]:
    _ = agent_id
    prior = registry.get_step(run_id, "no_trigger")
    if prior is not None:
        run = registry.get_run(run_id)
        assert run is not None
        return _result_from_run(run)

    registry.update_run(
        run_id,
        status="no_trigger",
        detection=json.dumps(detection),
        report="",
        verdict=json.dumps({}),
    )
    registry.checkpoint_step(run_id, "no_trigger", {"detection": detection})
    return {"run_id": run_id, "status": "no_trigger", "detection": detection,
            "last_step": "no_trigger"}


def _step_publish_impl(run_id: str, *, approver: str) -> dict[str, Any]:
    """Idempotent publish: same path overwrite; already-approved is a no-op success."""
    run = registry.get_run(run_id)
    if run is None:
        raise ValueError(f"no run {run_id!r}")

    if run["status"] == "approved" and run.get("artifact_path"):
        artifact = Path(run["artifact_path"])
        if artifact.is_file():
            registry.checkpoint_step(
                run_id, "publish",
                {"artifact_path": str(artifact), "idempotent": True})
            return {"run_id": run_id, "status": "approved",
                    "artifact_path": str(artifact)}

    if run["status"] != "pending_approval":
        raise PermissionError(f"run {run_id!r} is {run['status']!r}, not pending_approval")

    out_dir = OUTPUTS / approver / run_id
    out_dir.mkdir(parents=True, exist_ok=True)
    artifact = out_dir / "report.md"
    artifact.write_text(run.get("report") or "", encoding="utf-8")

    registry.update_run(run_id, status="approved", approver=approver,
                        artifact_path=str(artifact))
    registry.checkpoint_step(run_id, "publish", {"artifact_path": str(artifact)})
    _audit("agent.published",
           {"run_id": run_id, "agent_id": run["agent_id"], "approver": approver,
            "artifact": str(artifact)}, actor=approver)
    return {"run_id": run_id, "status": "approved", "artifact_path": str(artifact)}


def _agent_run_workflow_impl(agent_id: str, actor: str, run_id: str,
                             workflow_id: str) -> dict[str, Any]:
    """Durable detect→draft workflow. Parks at pending_approval (human gate)."""
    detection = step_detect(agent_id, actor=actor, run_id=run_id)
    if not detection.get("fired"):
        out = step_record_no_trigger(agent_id, run_id=run_id, detection=detection)
        out["workflow_id"] = workflow_id
        out["last_step"] = out.get("last_step") or "no_trigger"
        return out
    out = step_draft(agent_id, actor=actor, run_id=run_id, detection=detection)
    out["workflow_id"] = workflow_id
    out["last_step"] = out.get("last_step") or "draft"
    return out


# Public step aliases — rebound to DBOS wrappers when [agents] extra is installed.
step_detect: Callable[..., dict[str, Any]] = _step_detect_impl
step_draft: Callable[..., dict[str, Any]] = _step_draft_impl
step_record_no_trigger: Callable[..., dict[str, Any]] = _step_record_no_trigger_impl
step_publish: Callable[..., dict[str, Any]] = _step_publish_impl
agent_run_workflow: Callable[..., dict[str, Any]] = _agent_run_workflow_impl


def _bind_dbos_steps() -> None:
    """Annotate step / workflow callables with DBOS when the library is present."""
    global step_detect, step_draft, step_record_no_trigger, step_publish
    global agent_run_workflow, _dbos_bound_generation
    if not dbos_runtime.HAS_DBOS:
        return
    if not dbos_runtime.ensure_configured():
        return
    gen = dbos_runtime.generation()
    if _dbos_bound_generation == gen:
        return
    DBOS = dbos_runtime.DBOS
    assert DBOS is not None
    step_detect = DBOS.step(name="dms_step_detect")(_step_detect_impl)  # type: ignore[misc]
    step_draft = DBOS.step(name="dms_step_draft")(_step_draft_impl)  # type: ignore[misc]
    step_record_no_trigger = DBOS.step(name="dms_step_no_trigger")(  # type: ignore[misc]
        _step_record_no_trigger_impl)
    step_publish = DBOS.step(name="dms_step_publish")(_step_publish_impl)  # type: ignore[misc]
    agent_run_workflow = DBOS.workflow(name="dms_agent_run")(  # type: ignore[misc]
        _agent_run_workflow_impl)
    _dbos_bound_generation = gen


def run_agent(agent_id: str, *, actor: str = "system",
              workflow_id: str | None = None) -> dict[str, Any]:
    agent = registry.get_agent(agent_id)
    if agent is None:
        raise ValueError(f"no agent {agent_id!r}")

    workflow_id = workflow_id or uuid.uuid4().hex

    # Resume after draft (or terminal): do not create a second run / re-draft.
    existing = registry.get_run_by_workflow_id(workflow_id)
    if existing and existing.get("last_step") in ("draft", "no_trigger", "publish", "reject"):
        return _result_from_run(existing)

    run_id = existing["run_id"] if existing else registry.record_run(
        agent_id, status="running", detection={},
        workflow_id=workflow_id, last_step="start")
    if existing:
        registry.update_run(run_id, workflow_id=workflow_id)

    if dbos_runtime.HAS_DBOS:
        _bind_dbos_steps()
        dbos_runtime.ensure_launched()
        with dbos_runtime.set_workflow_id(workflow_id):
            return agent_run_workflow(agent_id, actor, run_id, workflow_id)
    return agent_run_workflow(agent_id, actor, run_id, workflow_id)


def approve_run(run_id: str, *, approver: str) -> dict[str, Any]:
    # Publish is always human-gated here; bind DBOS step if available.
    if dbos_runtime.HAS_DBOS:
        _bind_dbos_steps()
        dbos_runtime.ensure_launched()
    return step_publish(run_id, approver=approver)


def reject_run(run_id: str, *, approver: str, reason: str = "") -> dict[str, Any]:
    run = registry.get_run(run_id)
    if run is None:
        raise ValueError(f"no run {run_id!r}")
    if run["status"] == "rejected":
        return {"run_id": run_id, "status": "rejected"}
    if run["status"] != "pending_approval":
        raise PermissionError(f"run {run_id!r} is {run['status']!r}, not pending_approval")
    registry.update_run(run_id, status="rejected", approver=approver)
    registry.checkpoint_step(run_id, "reject", {"reason": reason})
    _audit("agent.rejected", {"run_id": run_id, "approver": approver, "reason": reason},
           actor=approver)
    return {"run_id": run_id, "status": "rejected"}
