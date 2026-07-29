"""Workflow runner — templates → parallel DAG phases → durable telemetry store.

This is the glue the Background tasks panel needs. Templates, the DAG runner,
agent_task, and workflow_store already exist; this module owns start/cancel,
phase ordering, fan_items recompile, Netie OOM caps, and OpenVault key hydrate.
"""

from __future__ import annotations

import asyncio
import json
import threading
import uuid
from collections.abc import Mapping
from typing import Any

from CortexOS.execution import workflow_oom, workflow_openvault, workflow_store, workflow_templates
from CortexOS.execution.dag_runner import ExecutionContext, run_dag
from CortexOS.execution.model_router import ModelRouter
from CortexOS.fabrication.dsl_parser import AgenticDSLProgram, DSLNode, NodeType
from CortexOS.routing.cost_ledger import CostLedger

DEFAULT_COST_CEILING_MYR = 25.0
_ACTIVE: dict[str, asyncio.Task] = {}
_LOOP: asyncio.AbstractEventLoop | None = None
_LOOP_THREAD: threading.Thread | None = None
_LOCK = threading.Lock()
_HARDWARE: dict[str, Any] = {}


def set_hardware(hw: Mapping[str, Any] | None) -> None:
    """AirGPT / engine probe can push live VRAM so fan-out stays OOM-safe."""
    global _HARDWARE
    _HARDWARE = dict(hw or {})


def get_hardware() -> dict[str, Any]:
    return dict(_HARDWARE)


def _ensure_loop() -> asyncio.AbstractEventLoop:
    global _LOOP, _LOOP_THREAD
    with _LOCK:
        if _LOOP is not None and _LOOP.is_running():
            return _LOOP

        loop = asyncio.new_event_loop()

        def _run() -> None:
            asyncio.set_event_loop(loop)
            loop.run_forever()

        t = threading.Thread(target=_run, name="wf-runner-loop", daemon=True)
        t.start()
        _LOOP = loop
        _LOOP_THREAD = t
        return loop


def list_workflows() -> list[dict[str, Any]]:
    return workflow_templates.catalog()


def _seed_prompt(variables: Mapping[str, Any]) -> str:
    for key in ("topic", "target", "goal", "prompt"):
        val = variables.get(key)
        if val:
            return str(val)
    return json.dumps(dict(variables), default=str)[:2000]


def _phase_rows(template: workflow_templates.WorkflowTemplate) -> list[dict[str, Any]]:
    return [
        {
            "id": p.id,
            "title": p.title,
            "detail": p.detail,
            "agent_total": sum(1 if not a.fan_over else max(1, a.fan_max) for a in p.agents),
        }
        for p in template.phases
    ]


def _agent_regs(
    template: workflow_templates.WorkflowTemplate,
    variables: Mapping[str, Any],
    fan_items: Mapping[str, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    regs: list[dict[str, Any]] = []
    for phase in template.phases:
        for node_id, spec, _vars in workflow_templates._expand(phase, template, variables, fan_items):
            regs.append(
                {
                    "phase": phase.id,
                    "node_id": node_id,
                    "label": f"{phase.id}:{spec.id}",
                    "purpose": spec.purpose,
                    "prompt_id": spec.prompt_id,
                    "tools": list(spec.tools),
                }
            )
    return regs


def _compile_phase(
    template: workflow_templates.WorkflowTemplate,
    phase: workflow_templates.PhaseSpec,
    variables: Mapping[str, Any],
    fan_items: Mapping[str, list[dict[str, Any]]],
    *,
    upstream_ids: list[str],
) -> AgenticDSLProgram:
    """One phase as its own mini-DAG so we can re-expand fan_over after upstream.

    Prior-phase join ids are re-introduced as ``DOCUMENT_REF`` stubs that read
    values already sitting on the shared ``ExecutionContext``.
    """
    from CortexOS.execution import prompt_library

    seed = DSLNode(id="prompt", kind=NodeType.DOCUMENT_REF, context_key="prompt")
    nodes: list[DSLNode] = [seed]
    seen = {"prompt"}
    for uid in upstream_ids:
        if not uid or uid in seen:
            continue
        nodes.append(DSLNode(id=uid, kind=NodeType.DOCUMENT_REF, context_key=uid))
        seen.add(uid)

    inputs = [u for u in upstream_ids if u in seen] or ["prompt"]
    agent_ids: list[str] = []
    for node_id, spec, prompt_vars in workflow_templates._expand(
        phase, template, variables, fan_items
    ):
        nodes.append(
            DSLNode(
                id=node_id,
                kind=NodeType.AGENT_TASK,
                inputs=list(inputs),
                prompt=prompt_library.render(spec.prompt_id, prompt_vars),
                max_tokens=spec.max_tokens,
                annotations={
                    "workflow": template.id,
                    "phase": phase.id,
                    "phase_title": phase.title,
                    "agent": spec.id,
                    "label": f"{phase.id}:{spec.id}",
                    "purpose": spec.purpose,
                    "prompt_id": spec.prompt_id,
                    "tools": list(spec.tools),
                    "effort": spec.effort,
                    "vars": dict(prompt_vars),
                },
            )
        )
        agent_ids.append(node_id)

    join_id = f"{phase.id}__join"
    nodes.append(
        DSLNode(
            id=join_id,
            kind=NodeType.EMIT,
            inputs=agent_ids or ["prompt"],
            annotations={"workflow": template.id, "phase": phase.id, "join": True},
        )
    )
    return AgenticDSLProgram(
        nodes=nodes,
        entry_node_id="prompt",
        output_node_id=join_id,
        intent_hash=f"wf:{template.id}:{phase.id}",
    )


def _extract_fan_items(outputs: Mapping[str, Any]) -> dict[str, list[dict[str, Any]]]:
    """Pull angles / findings / bugs lists out of agent JSON for the next fan_over."""
    found: dict[str, list[dict[str, Any]]] = {}
    for _nid, out in outputs.items():
        if not isinstance(out, dict):
            continue
        data = out.get("data") if isinstance(out.get("data"), dict) else out
        if not isinstance(data, dict):
            continue
        for key in ("angles", "findings", "bugs", "claims"):
            items = data.get(key)
            if isinstance(items, list) and items:
                bucket = found.setdefault(key, [])
                for item in items:
                    if isinstance(item, dict):
                        bucket.append(item)
                    elif isinstance(item, str):
                        bucket.append({"claim": item, "id": f"auto-{len(bucket)}"})
    return found


def _panel_task(run: dict[str, Any]) -> dict[str, Any]:
    """Shape one store run into the AirGPT Background tasks panel contract."""
    phases_out = []
    agents_all = run.get("agents") or []
    for ph in run.get("phases") or []:
        ph_agents = []
        for a in ph.get("agents") or [
            x for x in agents_all if x.get("phase_id") == ph.get("phase_id")
        ]:
            elapsed_ms = int(a.get("elapsed_ms") or 0)
            tools_allow = a.get("tools") or []
            if isinstance(tools_allow, str):
                try:
                    tools_allow = json.loads(tools_allow)
                except (TypeError, ValueError):
                    tools_allow = []
            detail = a.get("tool_detail") or []
            ph_agents.append(
                {
                    "id": a.get("label") or a.get("node_id"),
                    "node_id": a.get("node_id"),
                    "purpose": a.get("purpose") or "",
                    "status": a.get("status") or "pending",
                    "tokens": int(a.get("tokens") or 0),
                    "prompt_tokens": int(a.get("prompt_tokens") or 0),
                    "completion_tokens": int(a.get("completion_tokens") or 0),
                    "tool_count": int(a.get("tool_calls") or len(detail) or 0),
                    "tools": tools_allow,
                    "tool_detail": [
                        {
                            "tool": t.get("tool"),
                            "ok": bool(t.get("ok", True)),
                            "ms": int(t.get("ms") or 0),
                            "summary": (t.get("summary") or "")[:400],
                        }
                        for t in detail
                        if isinstance(t, dict)
                    ],
                    "excerpt": (a.get("excerpt") or "")[:2000],
                    "error": (a.get("error") or "")[:500],
                    "elapsed": round(elapsed_ms / 1000.0, 1),
                    "model": a.get("model") or "",
                    "tier": a.get("tier") or "",
                }
            )
        status = ph.get("status") or "pending"
        phases_out.append(
            {
                "id": ph.get("phase_id") or ph.get("id"),
                "name": ph.get("title") or ph.get("phase_id"),
                "detail": ph.get("detail") or "",
                "status": "done" if status == "done" else status,
                "agents": ph_agents,
            }
        )

    tokens = int(run.get("tokens") or 0)
    tools = int(run.get("tool_calls") or 0)
    phases_done = sum(1 for p in phases_out if p["status"] in ("done", "completed"))
    st = run.get("status") or "queued"
    if st == "completed":
        panel_status = "done"
    elif st == "stopped":
        panel_status = "cancelled"
    elif st == "error":
        panel_status = "error"
    else:
        panel_status = st

    return {
        "id": run.get("id"),
        "name": run.get("title") or run.get("template_id"),
        "kind": "Workflow",
        "description": run.get("purpose") or "",
        "template_id": run.get("template_id"),
        "status": panel_status,
        "elapsed": float(run.get("elapsed_s") or 0),
        "tokens_estimated": bool(run.get("tokens_estimated")),
        "totals": {
            "agents": int(run.get("agent_total") or len(agents_all)),
            "agents_done": int(run.get("agent_done") or 0),
            "tokens": tokens,
            "tools": tools,
            "phases_done": phases_done,
            "phases_total": len(phases_out),
            "cost_myr": float(run.get("cost_myr") or 0),
        },
        "phases": phases_out,
        "error": run.get("error") or "",
        "started_at": run.get("started_at") or run.get("created_at"),
        "ended_at": run.get("finished_at"),
    }


def snapshot() -> dict[str, Any]:
    active = workflow_store.list_runs(limit=40, status="active")
    finished_raw = [
        r
        for r in workflow_store.list_runs(limit=40)
        if r.get("status") in ("completed", "error", "stopped")
    ]
    # list_runs(active) lacks nested agents — hydrate via get_run for panel fidelity.
    running = [_panel_task(workflow_store.get_run(r["id"])) for r in active if r.get("id")]
    finished = [
        _panel_task(workflow_store.get_run(r["id"])) for r in finished_raw if r.get("id")
    ]
    return {"ok": True, "running": running, "finished": finished}


def get_task(task_id: str) -> dict[str, Any] | None:
    run = workflow_store.get_run(task_id)
    if not run:
        return None
    return _panel_task(run)


def cancel(task_id: str) -> dict[str, Any]:
    ok = workflow_store.request_cancel(task_id)
    if not ok:
        return {"ok": False, "error": "unknown or finished task"}
    return {"ok": True, "task_id": task_id}


def clear_finished() -> dict[str, Any]:
    n = workflow_store.clear_finished(keep=0)
    return {"ok": True, "cleared": n}


def _make_on_event(run_id: str):
    def on_event(ev: dict[str, Any]) -> None:
        et = ev.get("type")
        node = str(ev.get("node") or "")
        kind_s = str(ev.get("kind") or "").lower()
        if et == "node_start" and "agent_task" in kind_s:
            workflow_store.agent_started(
                run_id,
                node,
                phase=ev.get("phase") or "",
                label=ev.get("label") or node,
                purpose=ev.get("purpose") or "",
            )
            if ev.get("phase"):
                workflow_store.phase_started(run_id, str(ev["phase"]))
        elif et == "node_done":
            tel = ev.get("telemetry") if isinstance(ev.get("telemetry"), dict) else {}
            # EMIT joins have no telemetry — skip agent_finished for them.
            if tel:
                if not (tel.get("prompt_tokens") or tel.get("completion_tokens")):
                    workflow_store.mark_tokens_estimated(run_id)
                workflow_store.agent_finished(
                    run_id,
                    node,
                    tel,
                    status="done",
                    excerpt=str(ev.get("excerpt") or "")[:2000],
                )
        elif et == "node_error":
            if "agent_task" in kind_s or node:
                workflow_store.agent_finished(
                    run_id,
                    node,
                    {"error": ev.get("error") or ""},
                    status="error",
                    error=str(ev.get("error") or ""),
                )
        elif et == "agent_tool":
            workflow_store.note_tool_call(
                run_id,
                node,
                str(ev.get("tool") or ""),
                ok=bool(ev.get("ok", True)),
                ms=int(ev.get("ms") or 0),
            )

    return on_event


async def _execute_run(
    run_id: str,
    template: workflow_templates.WorkflowTemplate,
    variables: dict[str, Any],
    *,
    cost_ceiling_myr: float,
    router: ModelRouter | None,
    ledger: CostLedger | None,
    spill_high_effort: bool,
    max_parallel: int,
    resume: bool = False,
) -> None:
    router = router or ModelRouter()
    ledger = ledger or CostLedger()
    # Cloud spill: force high-effort nodes onto BIG_API path via provider override.
    if spill_high_effort:
        # ModelRouter maps None+T3 → cloud; agent_task already bumps high → T2/T3.
        pass

    workflow_openvault.ensure_provider_keys()
    budget = workflow_openvault.check_openfree_budget(
        workflow_openvault.workflow_identity(run_id),
        prompt_tokens=800 * max(1, template.agent_count),
        max_tokens=1400 * max(1, template.agent_count),
    )
    if not budget.get("ok"):
        workflow_store.finish_run(
            run_id, status="error", error=str(budget.get("error") or "budget")
        )
        return

    seed = {
        "prompt": _seed_prompt(variables),
        **{k: v for k, v in variables.items() if isinstance(v, (str, int, float, bool))},
    }
    ctx = ExecutionContext(run_id, seed)
    on_event = _make_on_event(run_id)
    fan_items: dict[str, list[dict[str, Any]]] = {}
    upstream_ids: list[str] = []
    last_join = ""
    result_blob = ""

    workflow_store.start_run(run_id)
    workflow_store.register_agents(run_id, _agent_regs(template, variables, fan_items))

    try:
        for phase in template.phases:
            if workflow_store.is_cancelled(run_id):
                break

            # Re-register once fan width is known so the panel shows N verify agents.
            if fan_items:
                workflow_store.register_agents(
                    run_id, _agent_regs(template, variables, fan_items)
                )

            workflow_store.phase_started(run_id, phase.id)
            dag = _compile_phase(
                template,
                phase,
                variables,
                fan_items,
                upstream_ids=upstream_ids,
            )

            # Tag high-effort agents for cloud when local concurrency is tight.
            if spill_high_effort:
                for node in dag.nodes:
                    ann = node.annotations if isinstance(node.annotations, dict) else {}
                    if str(ann.get("effort") or "").lower() == "high":
                        node.provider = "anthropic"

            await run_dag(
                dag,
                ctx,
                router,
                ledger,
                workflow_cost_ceiling_myr=cost_ceiling_myr,
                parallel=True,
                max_parallel=max_parallel,
                should_abort=lambda: workflow_store.is_cancelled(run_id),
                on_event=on_event,
                resume=resume,
            )
            workflow_store.phase_finished(run_id, phase.id)

            # Collect outputs for fan extraction + next-phase upstream.
            phase_outs: dict[str, Any] = {}
            for nid, val in list(ctx._data.items()):
                if isinstance(nid, str) and nid.startswith(f"{phase.id}__") and not nid.endswith("__join"):
                    phase_outs[nid] = val
            newly = _extract_fan_items(phase_outs)
            for k, items in newly.items():
                fan_items.setdefault(k, []).extend(items)

            last_join = f"{phase.id}__join"
            upstream_ids = [last_join] if last_join in ctx._data else list(phase_outs.keys())[:8]

            join_out = ctx.get(last_join)
            if isinstance(join_out, dict):
                result_blob = json.dumps(join_out, default=str)[:20000]

        if workflow_store.is_cancelled(run_id):
            return
        workflow_store.finish_run(run_id, status="completed", result=result_blob)
    except Exception as exc:
        if workflow_store.is_cancelled(run_id):
            return
        msg = f"{type(exc).__name__}: {exc}"[:1000]
        workflow_store.finish_run(run_id, status="error", error=msg)


def resume(
    run_id: str,
    *,
    cost_ceiling_myr: float | None = None,
    router: ModelRouter | None = None,
    ledger: CostLedger | None = None,
    hardware: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Re-execute a finished run under its original id, replaying journaled nodes.

    Nodes this run already completed come back from the step journal instead of
    being re-run, so a run that died in its last phase does not pay for the
    phases that already succeeded (distill: Claude Code workflow resume).
    """
    run = workflow_store.get_run(run_id)
    if not run:
        return {"ok": False, "error": f"unknown run: {run_id}"}
    with _LOCK:
        active = run_id in _ACTIVE
    if active or run.get("status") == "running":
        return {"ok": False, "error": "run is still active", "run_id": run_id}

    template = workflow_templates.get(str(run.get("template_id") or ""))
    if not template:
        return {"ok": False, "error": f"unknown workflow: {run.get('template_id')}"}

    hw = dict(hardware or _HARDWARE)
    gate = workflow_oom.resolve_max_parallel(hw, requested=workflow_oom.DEFAULT_MAX_PARALLEL)
    workflow_store.create_run(
        run_id,
        template_id=template.id,
        title=template.name,
        purpose=template.description,
        prompt=str(run.get("prompt") or ""),
        origin=str(run.get("origin") or "resume"),
        session_id=str(run.get("session_id") or ""),
        actor=str(run.get("actor") or ""),
        variables=dict(run.get("vars") or {}),
        phases=_phase_rows(template),
    )

    loop = _ensure_loop()
    coro = _execute_run(
        run_id,
        template,
        dict(run.get("vars") or {}),
        cost_ceiling_myr=float(
            cost_ceiling_myr if cost_ceiling_myr is not None else DEFAULT_COST_CEILING_MYR
        ),
        router=router,
        ledger=ledger,
        spill_high_effort=bool(gate.get("spill_high_effort_to_cloud")),
        max_parallel=int(gate.get("max_parallel") or 4),
        resume=True,
    )
    fut = asyncio.run_coroutine_threadsafe(coro, loop)
    with _LOCK:
        _ACTIVE[run_id] = fut  # type: ignore[assignment]

    def _done(_f: Any) -> None:
        with _LOCK:
            _ACTIVE.pop(run_id, None)

    fut.add_done_callback(_done)
    return {
        "ok": True,
        "resumed": True,
        "task_id": run_id,
        "run_id": run_id,
        "template_id": template.id,
        "task": get_task(run_id),
    }


def start(
    template_id: str = "",
    *,
    goal: str = "",
    topic: str = "",
    target: str = "",
    breadth: int = 3,
    variables: dict[str, Any] | None = None,
    origin: str = "api",
    session_id: str = "",
    actor: str = "",
    cost_ceiling_myr: float | None = None,
    router: ModelRouter | None = None,
    ledger: CostLedger | None = None,
    hardware: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Create a durable run and execute it on the background asyncio loop."""
    tid = (template_id or "").strip().lower()
    # Aliases from AirGPT WIP / screenshots.
    aliases = {
        "netie-smoothness-audit": "smoothness_audit",
        "deep-research": "deep_research",
        "research": "deep_research",
    }
    tid = aliases.get(tid, tid)

    vars_in = dict(variables or {})
    text = (goal or topic or target or vars_in.get("topic") or vars_in.get("target") or "").strip()
    if not tid and text:
        from CortexOS.execution.workflow_recognizer import recognize

        rec = recognize(text)
        if rec.template_id:
            tid = rec.template_id
            vars_in.update(rec.variables)

    if not tid:
        # Empty workflow_id + goal → deep research (matches AirGPT bgTasks.research).
        tid = "deep_research" if text else ""
    template = workflow_templates.get(tid)
    if not template:
        return {"ok": False, "error": f"unknown workflow: {template_id or tid or '(empty)'}"}

    for key, val in (("topic", topic or goal or text), ("target", target or text), ("goal", goal or text)):
        if val and key in template.inputs and key not in vars_in:
            vars_in[key] = val
        elif val and key not in vars_in:
            vars_in[key] = val
    if "fanout" not in vars_in and tid == "deep_research":
        vars_in["fanout"] = max(2, min(6, int(breadth or 3)))

    hw = dict(hardware or _HARDWARE)
    gate = workflow_oom.resolve_max_parallel(hw, requested=workflow_oom.DEFAULT_MAX_PARALLEL)
    run_id = f"wf_{uuid.uuid4().hex[:12]}"
    workflow_store.create_run(
        run_id,
        template_id=template.id,
        title=template.name,
        purpose=template.description,
        prompt=_seed_prompt(vars_in),
        origin=origin,
        session_id=session_id,
        actor=actor,
        variables=vars_in,
        phases=_phase_rows(template),
    )

    loop = _ensure_loop()
    coro = _execute_run(
        run_id,
        template,
        vars_in,
        cost_ceiling_myr=float(
            cost_ceiling_myr if cost_ceiling_myr is not None else DEFAULT_COST_CEILING_MYR
        ),
        router=router,
        ledger=ledger,
        spill_high_effort=bool(gate.get("spill_high_effort_to_cloud")),
        max_parallel=int(gate.get("max_parallel") or 4),
    )
    fut = asyncio.run_coroutine_threadsafe(coro, loop)
    with _LOCK:
        _ACTIVE[run_id] = fut  # type: ignore[assignment]

    def _done(_f: Any) -> None:
        with _LOCK:
            _ACTIVE.pop(run_id, None)

    fut.add_done_callback(_done)

    return {
        "ok": True,
        "task_id": run_id,
        "run_id": run_id,
        "template_id": template.id,
        "max_parallel": gate.get("max_parallel"),
        "oom_gate": gate,
        "task": get_task(run_id),
    }
