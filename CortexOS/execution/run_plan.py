"""Dispatch architecture run plans to existing Cortex execution paths."""

from __future__ import annotations

import json
import uuid
from typing import Any, Mapping

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
    require_extra("agentic", feature="rag_compose")
    require_extra("rag", feature="rag_compose")
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
    if runner == "rag_compose":
        return await _rag_compose(plan, body)
    if runner == "memory_compose":
        return _memory_compose(plan, body)
    if runner == "ontology_actions":
        return _ontology_actions(plan, body, caller)
    if runner in {"marketplace_langgraph", "marketplace_langchain"}:
        return {"ok": False, "runner": runner, "status_code": 501, "error": "adapter_unavailable"}
    return {"ok": False, "runner": runner, "status_code": 400, "error": "unknown_runner"}
