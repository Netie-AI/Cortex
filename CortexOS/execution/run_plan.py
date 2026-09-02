"""Dispatch architecture run plans to existing Cortex execution paths."""

from __future__ import annotations

import json
import uuid
from collections.abc import Mapping
from typing import Any

from CortexOS.fabrication.dsl_parser import AgenticDSLProgram, DSLNode, NodeType, parse_dsl
from CortexOS.packaging import require_extra
from CortexOS.result import Ok
from CortexOS.routing.cost_ledger import CostLedger


def _request_value(plan: Mapping[str, Any], body: Mapping[str, Any], key: str, default: Any = None) -> Any:
    """Prefer the raw request while accepting plans produced by ``plan_for_request``."""
    if key in body:
        return body[key]
    request = plan.get("request")
    if isinstance(request, Mapping):
        return request.get(key, default)
    return default


def _inline_dag(runner: str) -> AgenticDSLProgram:
    """Build deterministic DAGs so A2 dispatch never triggers model inference."""
    prompt_node = DSLNode(id="prompt", kind=NodeType.DOCUMENT_REF, context_key="prompt")
    if runner == "dag_single":
        return AgenticDSLProgram(
            nodes=[prompt_node],
            entry_node_id="prompt",
            output_node_id="prompt",
        )

    first_emit = DSLNode(id="emit_prompt", kind=NodeType.EMIT, inputs=["prompt"])
    final_emit = DSLNode(id="emit_result", kind=NodeType.EMIT, inputs=["emit_prompt"])
    return AgenticDSLProgram(
        nodes=[prompt_node, first_emit, final_emit],
        entry_node_id="prompt",
        output_node_id="emit_result",
    )


def _parse_supplied_dag(raw_dag: Any) -> AgenticDSLProgram:
    if not isinstance(raw_dag, Mapping):
        raise ValueError("dag must be a JSON object")
    parsed = parse_dsl(json.dumps(dict(raw_dag)), intent_hash="engine_run")
    if not isinstance(parsed, Ok):
        raise ValueError(parsed.message)
    return parsed.value


def _serialize_dag_result(result: Any) -> dict[str, dict[str, Any]]:
    return {
        node_id: {
            "output": node_result.output,
            "tier": node_result.tier,
            "cost_myr": node_result.cost_myr,
        }
        for node_id, node_result in result.outputs.items()
    }


async def _execute_dag(plan: Mapping[str, Any], body: Mapping[str, Any]) -> dict[str, Any]:
    require_extra("agentic", feature="dag_runner")
    from CortexOS.execution.dag_runner import ExecutionContext, run_dag
    from CortexOS.execution.model_router import ModelRouter

    runner = str(plan["runner"])
    supplied_dag = body.get("dag") if runner == "dag_runner" else None
    dag = _parse_supplied_dag(supplied_dag) if supplied_dag is not None else _inline_dag(runner)
    prompt = str(_request_value(plan, body, "prompt", "") or "")
    run_id = str(_request_value(plan, body, "session_id") or uuid.uuid4())
    seed = {
        "prompt": prompt,
        "query": str(body.get("query") or prompt),
        "params": body.get("params") or {},
        "rag_corpus": body.get("rag_corpus") or body.get("corpus") or [],
        "actor": body.get("actor"),
    }
    if body.get("_a2a_transport") is not None:
        seed["_a2a_transport"] = body["_a2a_transport"]
    context = ExecutionContext(run_id, seed)
    result = await run_dag(dag, context, ModelRouter(), CostLedger())
    nodes = _serialize_dag_result(result)
    return {
        "ok": True,
        "runner": runner,
        "status": "completed",
        "run_id": run_id,
        "nodes": nodes,
        "output": nodes.get(dag.output_node_id, {}).get("output"),
    }


async def _rag_compose(plan: Mapping[str, Any], body: Mapping[str, Any]) -> dict[str, Any]:
    # Only the DAG runner is gated. The rag_* nodes compose over the corpus in
    # the request body using first-party lexical code — no qdrant, no tantivy,
    # no embedding model — so the rag extra is not required to get here.
    require_extra("agentic", feature="rag_compose")
    from CortexOS.execution.dag_runner import ExecutionContext, run_dag
    from CortexOS.execution.model_router import ModelRouter
    from CortexOS.rag.templates import get_template

    depth = str(body.get("depth") or body.get("template") or "high").lower()
    dag = get_template(depth)
    prompt = str(_request_value(plan, body, "prompt", "") or body.get("query") or "")
    run_id = str(_request_value(plan, body, "session_id") or uuid.uuid4())
    context = ExecutionContext(
        run_id,
        {
            "prompt": prompt,
            "query": str(body.get("query") or prompt),
            "rag_corpus": body.get("rag_corpus") or body.get("corpus") or [],
            "actor": body.get("actor"),
        },
    )
    result = await run_dag(dag, context, ModelRouter(), CostLedger())
    nodes = _serialize_dag_result(result)
    out = nodes.get(dag.output_node_id, {}).get("output") or {}
    hits = []
    if isinstance(out, dict):
        hits = out.get("citations") or []
    return {
        "ok": True,
        "runner": str(plan["runner"]),
        "status": "completed",
        "template": dag.intent_hash,
        "depth": depth,
        "run_id": run_id,
        "nodes": nodes,
        "output": out,
        "hits": hits,
    }


def _memory_compose(plan: Mapping[str, Any], body: Mapping[str, Any]) -> dict[str, Any]:
    vector = body.get("vector") or (body.get("params") or {}).get("vector")
    hits: list[dict[str, Any]] = []
    if isinstance(vector, list) and all(isinstance(value, (int, float)) for value in vector):
        from CortexOS.api.memory_routes import _STORE

        for hit in _STORE.query([float(value) for value in vector], k=5):
            hits.append({"id": hit.id, "score": round(hit.score, 6), "text": hit.text, "meta": hit.meta})
    return {
        "ok": True,
        "runner": str(plan["runner"]),
        "status": "composed",
        "hits": hits,
    }


_DEFAULT_AGENT_TOOLS = ["web_search", "web_fetch"]
_DEFAULT_QUALITY = [
    "addresses the user prompt",
    "does not invent tool results",
]


def _agent_verify_fn(router: Any, ledger: CostLedger, run_id: str, ceiling: float):
    """Independent of the generator: different request_type, never AGENT_TASK."""

    async def verify(content: str, criteria: list[str], step: int) -> dict[str, Any]:
        from CortexOS.execution.executor import invoke_routed_completion
        from CortexOS.execution.generator_verifier import parse_verifier_payload
        from CortexOS.execution.model_router import ModelRequest
        from CortexOS.routing.adapters.base import AdapterRequest
        from CortexOS.routing.tiers import Tier

        crit = "\n".join(f"- {c}" for c in criteria)
        prompt = (
            "Independent verifier. The generator is a different model call.\n"
            f"Step {step}. Criteria:\n{crit}\n\nAnswer:\n{content[:6000]}\n\n"
            'Return JSON only: {"passed": bool, "feedback": str, "criteria_checked": [str]}'
        )
        try:
            outcome = await invoke_routed_completion(
                router,
                ledger,
                run_id=run_id,
                workflow_cost_ceiling_myr=ceiling,
                node_id="agent_verify",
                model_req=ModelRequest(
                    request_type="agent_task.verify",
                    prompt=prompt,
                    default_tier=Tier("T1"),
                    max_tier=Tier("T2"),
                    cost_ceiling_myr=ceiling,
                ),
                adapter_req=AdapterRequest(
                    model="",
                    system="Verifier. JSON only. Never rubber-stamp.",
                    prompt=prompt,
                    max_tokens=400,
                ),
            )
            text = outcome.response.content or ""
        except Exception as exc:  # noqa: BLE001 — fail closed
            return {
                "passed": False,
                "feedback": f"verifier error: {exc}"[:300],
                "criteria_checked": [],
            }
        parsed = parse_verifier_payload(text, criteria=criteria)
        return {
            "passed": parsed.passed,
            "feedback": parsed.feedback,
            "criteria_checked": parsed.criteria_checked,
        }

    return verify


async def _execute_agent_task(plan: Mapping[str, Any], body: Mapping[str, Any]) -> dict[str, Any]:
    """One AGENT_TASK with web tools + independent quality stop (F20b / LOOP-02)."""
    require_extra("agentic", feature="agent_task")

    from CortexOS.execution.agent_task import run_agent_task
    from CortexOS.execution.dag_runner import ExecutionContext
    from CortexOS.execution.model_router import ModelRouter
    from CortexOS.execution.workflow_openvault import ensure_provider_keys
    from CortexOS.integrations.openvault_client import openvault_base_url, ping as ov_ping
    from CortexOS.routing.adapters import default_adapter_registry
    from CortexOS.routing.adapters.openvault import OpenVaultAdapter, resolved_openvault_token
    from CortexOS.routing.tiers import Tier

    ensure_provider_keys()
    prompt = str(_request_value(plan, body, "prompt", "") or "")
    run_id = str(_request_value(plan, body, "session_id") or uuid.uuid4())
    tools = [str(t) for t in (body.get("tools") or _DEFAULT_AGENT_TOOLS) if str(t).strip()]
    criteria = [
        str(c).strip()
        for c in (body.get("quality_criteria") or _DEFAULT_QUALITY)
        if str(c).strip()
    ]
    max_steps = max(1, min(int(body.get("max_steps") or 8), 20))
    ceiling = 1.0
    ov = openvault_base_url()
    api_base = f"{ov}/v1"
    registry = default_adapter_registry(vllm_base_url=api_base)
    if ov_ping():
        # Loopback OV: no bearer. Dummy tokens 401. Distill: crew-openvault-desktop.
        ov_adapter = OpenVaultAdapter(api_base=api_base, api_key=resolved_openvault_token())
        registry["self_hosted"] = ov_adapter
        registry["anthropic"] = ov_adapter
        registry["openai"] = ov_adapter
    router = ModelRouter(
        adapter_registry=registry,
        vllm_base_url=api_base,
        tier_models={
            Tier.T0: "auto",
            Tier.T1: "auto",
            Tier.T2: "auto",
            Tier.T3: "auto",
        },
    )
    ledger = CostLedger()
    params = body.get("params") if isinstance(body.get("params"), dict) else {}
    pid = str(body.get("prompt_id") or params.get("prompt_id") or "").strip()
    stage = str(params.get("stage") or "research")
    topic = str(params.get("topic") or prompt)[:4000]
    system = ""
    if pid:
        from CortexOS.execution import prompt_library

        try:
            system = prompt_library.render(pid, {"topic": topic, "stage": stage})
        except KeyError as exc:
            raise ValueError(f"unknown prompt_id {pid}") from exc
        if not str(system or "").strip():
            raise ValueError(f"prompt_id {pid} rendered empty")
    mt_raw = body.get("max_tokens")
    if mt_raw is None:
        mt_raw = params.get("max_tokens")
    if mt_raw is not None:
        max_tokens = max(256, min(int(mt_raw), 8192))
    elif pid == "research.idea_to_paper":
        max_tokens = 4096
    else:
        max_tokens = None
    node = DSLNode(
        id="loop",
        kind=NodeType.AGENT_TASK,
        inputs=[],
        prompt=prompt,
        system=system or None,
        max_tokens=max_tokens,
        annotations={
            "label": "engine-agent",
            "purpose": "quality-terminated tool loop",
            "tools": tools,
            "quality_criteria": criteria,
            "max_steps": max_steps,
            "prompt_id": pid or "engine-agent",
        },
    )
    ctx = ExecutionContext(
        run_id,
        {
            "prompt": prompt,
            "_quality_verify": _agent_verify_fn(router, ledger, run_id, ceiling),
        },
    )
    output, telemetry = await run_agent_task(
        node, ctx, router, ledger, workflow_cost_ceiling_myr=ceiling
    )
    stop = (
        (output.get("stop_reason") if isinstance(output, dict) else None)
        or telemetry.stop_reason
    )
    err = str(telemetry.error or "")[:300]
    return {
        "ok": stop != "error",
        "runner": "agent_task",
        "status": "completed" if stop != "error" else "error",
        "run_id": run_id,
        "output": output.get("content") if isinstance(output, dict) else output,
        "content": output.get("content") if isinstance(output, dict) else str(output),
        "stop_reason": stop,
        "quality_passed": output.get("quality_passed")
        if isinstance(output, dict)
        else telemetry.quality_passed,
        "error": err,
        "telemetry": telemetry.as_dict(),
        "node": output if isinstance(output, dict) else {"content": str(output)},
    }


def _ontology_actions(plan: Mapping[str, Any], body: Mapping[str, Any], caller: Any) -> dict[str, Any]:
    action_id = body.get("action_id")
    if not action_id:
        return {"ok": True, "runner": str(plan["runner"]), "status": "ready"}

    from CortexOS.agent_sdk import SdkDenied, call_action

    try:
        result = call_action(
            str(action_id),
            dict(body.get("params") or {}),
            actor=caller,
            run_id=str(body.get("session_id") or uuid.uuid4()),
            pack="dms",
        )
    except SdkDenied as exc:
        return {
            "ok": False,
            "runner": str(plan["runner"]),
            "status": "denied",
            "error": str(exc),
            "verdict": exc.verdict,
        }
    return {"ok": bool(result.get("ok", True)), "runner": str(plan["runner"]), "status": "completed", "result": result}


async def execute_run_plan(
    plan: Mapping[str, Any],
    body: Mapping[str, Any],
    *,
    caller: Any = None,
) -> dict[str, Any]:
    """Execute a resolved runner plan without introducing another orchestrator."""
    require_extra("agentic", feature="execute_run_plan")
    runner = str(plan.get("runner") or "")
    if runner in {"dag_single", "dag_linear", "dag_runner"}:
        return await _execute_dag(plan, body)
    if runner == "agent_task":
        return await _execute_agent_task(plan, body)
    if runner == "rag_compose":
        return await _rag_compose(plan, body)
    if runner == "memory_compose":
        return _memory_compose(plan, body)
    if runner == "ontology_actions":
        return _ontology_actions(plan, body, caller)
    if runner in {"marketplace_langgraph", "marketplace_langchain"}:
        return {"ok": False, "runner": runner, "status_code": 501, "error": "adapter_unavailable"}
    return {"ok": False, "runner": runner, "status_code": 400, "error": "unknown_runner"}
