"""gen-cFSM P0 — GENERATE → COMPILE dry-run on the existing DAG stack.

The LLM (later) emits a bounded IR; this module is the constrained gate in
front of dag_runner: horizon ∈ {3,5,7}, node alphabet restricted Ctrl-G
style, cycles rejected 100% before anything compiles. The collapse router
implements the G1 decision table (docs/research/findings/G1_GEN_CFSM_JEPA.md):
collapse ≥ τ + predicates → TERMINATE; collapse ≥ τ without predicates →
AUDIT_FAIL; Δcollapse > ε → CONTINUE; stalled k steps → REGENERATE;
step_count ≥ H → FORCE_AUDIT_END. No third orchestrator — IR compiles into
AgenticDSLProgram via parse_dsl and runs on dag_runner.
"""

from __future__ import annotations

import json
import time
import uuid
from collections.abc import Callable, Mapping
from typing import Any

from CortexOS.execution import scoreboard
from CortexOS.execution.dag_runner import ExecutionContext, run_dag
from CortexOS.execution.model_router import ModelRouter
from CortexOS.fabrication.dsl_parser import NodeType, parse_dsl
from CortexOS.memory.store import cosine
from CortexOS.result import Ok
from CortexOS.routing.cost_ledger import CostLedger

ALLOWED_HORIZONS: tuple[int, ...] = (3, 5, 7)

# Ctrl-G style restricted alphabet: kinds a generated cFSM node may use.
# Open-ended inference kinds (INFER_REMOTE, LLM_STREAM, SPECULATIVE_LLM) are
# deliberately excluded — generation must go through judged/bounded kinds.
CFSM_NODE_ALPHABET: frozenset[str] = frozenset(
    {
        NodeType.DOCUMENT_REF.value,
        NodeType.DETERMINISTIC_RULE.value,
        NodeType.TOOL_CALL.value,
        NodeType.RAG_RETRIEVE.value,
        NodeType.RAG_RERANK.value,
        NodeType.RAG_ANSWER.value,
        NodeType.AGENT_TASK.value,
        NodeType.LLM_JUDGED.value,
        NodeType.LLM_CACHED.value,
        NodeType.EMIT.value,
    }
)

DEFAULT_TAU = 0.85
DEFAULT_EPSILON = 0.02
DEFAULT_STALL_K = 2

DECISION_TERMINATE = "TERMINATE_SUCCESS"
DECISION_AUDIT_FAIL = "AUDIT_FAIL"
DECISION_CONTINUE = "CONTINUE"
DECISION_REGENERATE = "REGENERATE"
DECISION_FORCE_AUDIT = "FORCE_AUDIT_END"


def generate_ir(goal: str, horizon: int = 3) -> dict[str, Any]:
    """Deterministic P0 generator: |V| == horizon, linear chain into one EMIT.

    The future LLM generator replaces this function only — validate/compile
    stay the constrained gate either way.
    """
    nodes: list[dict[str, Any]] = []
    prev: str | None = None
    for i in range(1, horizon):
        node_id = f"step_{i}"
        node: dict[str, Any] = {
            "id": node_id,
            "kind": NodeType.DOCUMENT_REF.value,
            "context_key": "prompt",
        }
        if prev is not None:
            node["inputs"] = [prev]
        nodes.append(node)
        prev = node_id
    nodes.append(
        {
            "id": "audit",
            "kind": NodeType.EMIT.value,
            "inputs": [prev] if prev else [],
        }
    )
    return {
        "goal": goal,
        "horizon": horizon,
        "entry_node_id": "step_1" if horizon > 1 else "audit",
        "output_node_id": "audit",
        "predicates": [{"type": "nonempty"}],
        "nodes": nodes,
    }


def _node_kind(node: Mapping[str, Any]) -> str:
    return str(node.get("kind") or node.get("type") or "")


def _has_cycle(nodes: list[Mapping[str, Any]]) -> bool:
    """Kahn's algorithm over inputs edges — any un-poppable node means a cycle."""
    ids = [str(n.get("id")) for n in nodes]
    id_set = set(ids)
    indegree: dict[str, int] = {i: 0 for i in ids}
    dependents: dict[str, list[str]] = {i: [] for i in ids}
    for n in nodes:
        nid = str(n.get("id"))
        for inp in n.get("inputs") or []:
            if inp in id_set:
                indegree[nid] += 1
                dependents[str(inp)].append(nid)
    queue = [i for i in ids if indegree[i] == 0]
    seen = 0
    while queue:
        current = queue.pop()
        seen += 1
        for dep in dependents[current]:
            indegree[dep] -= 1
            if indegree[dep] == 0:
                queue.append(dep)
    return seen != len(ids)


def validate_ir(ir: Mapping[str, Any]) -> list[str]:
    """Return every constraint violation; empty list means compile-eligible."""
    errors: list[str] = []
    horizon = ir.get("horizon")
    if horizon not in ALLOWED_HORIZONS:
        errors.append(f"horizon_invalid:{horizon}")

    nodes = ir.get("nodes")
    if not isinstance(nodes, list) or not nodes:
        errors.append("nodes_missing")
        return errors

    if horizon in ALLOWED_HORIZONS and len(nodes) != horizon:
        errors.append(f"node_count_mismatch:{len(nodes)}!={horizon}")

    ids: set[str] = set()
    emit_ids: list[str] = []
    for node in nodes:
        nid = str(node.get("id") or "")
        if not nid:
            errors.append("node_id_missing")
            continue
        if nid in ids:
            errors.append(f"duplicate_node_id:{nid}")
        ids.add(nid)
        kind = _node_kind(node)
        if kind not in CFSM_NODE_ALPHABET:
            errors.append(f"kind_not_in_alphabet:{kind or 'missing'}")
        if kind == NodeType.EMIT.value:
            emit_ids.append(nid)
        for inp in node.get("inputs") or []:
            if inp == nid:
                errors.append(f"self_loop:{nid}")

    for node in nodes:
        for inp in node.get("inputs") or []:
            if inp not in ids:
                errors.append(f"input_unknown:{inp}")

    if len(emit_ids) != 1:
        errors.append(f"emit_count_invalid:{len(emit_ids)}")

    entry = ir.get("entry_node_id")
    output = ir.get("output_node_id")
    if entry not in ids:
        errors.append(f"entry_unknown:{entry}")
    if output not in ids:
        errors.append(f"output_unknown:{output}")
    elif emit_ids and output not in emit_ids:
        errors.append(f"output_not_emit:{output}")

    if _has_cycle(nodes):
        errors.append("cycle_detected")

    return errors


def compile_ir(ir: Mapping[str, Any]) -> dict[str, Any]:
    """Validate then compile through the real parser — never a second compiler."""
    errors = validate_ir(ir)
    if errors:
        return {"ok": False, "errors": errors, "program": None}

    envelope = {
        "nodes": [dict(n) for n in ir["nodes"]],
        "entry_node_id": ir["entry_node_id"],
        "output_node_id": ir["output_node_id"],
    }
    intent_hash = f"gen_cfsm_h{ir['horizon']}"
    parsed = parse_dsl(json.dumps(envelope), intent_hash=intent_hash)
    if not isinstance(parsed, Ok):
        return {"ok": False, "errors": [f"parse:{parsed.message}"], "program": None}
    return {"ok": True, "errors": [], "program": parsed.value, "intent_hash": intent_hash}


def dry_run(goal: str, horizon: int = 3) -> dict[str, Any]:
    """The P0 gate: GENERATE → COMPILE, no execution, no model inference."""
    ir = generate_ir(goal, horizon)
    compiled = compile_ir(ir)
    return {
        "ok": compiled["ok"],
        "goal": goal,
        "horizon": horizon,
        "node_count": len(ir.get("nodes") or []),
        "errors": compiled["errors"],
        "intent_hash": compiled.get("intent_hash"),
        "predicates": ir.get("predicates") or [],
    }


def collapse_score(state_vec: list[float], goal_vec: list[float]) -> float:
    """collapse = cosine(emb(state), emb(goal)); shaped toward −V(s,g) later."""
    return cosine(state_vec, goal_vec)


def route_step(
    *,
    collapse: float,
    prev_collapse: float,
    predicates_pass: bool,
    step_count: int,
    horizon: int,
    tau: float = DEFAULT_TAU,
    epsilon: float = DEFAULT_EPSILON,
    stall_count: int = 0,
    stall_k: int = DEFAULT_STALL_K,
) -> dict[str, Any]:
    """G1 collapse-router decision table. Returns decision + updated stall count."""
    if step_count >= horizon:
        return {"decision": DECISION_FORCE_AUDIT, "stall_count": stall_count}
    if collapse >= tau:
        if predicates_pass:
            return {"decision": DECISION_TERMINATE, "stall_count": 0}
        return {"decision": DECISION_AUDIT_FAIL, "stall_count": stall_count}
    delta = collapse - prev_collapse
    if delta > epsilon:
        return {"decision": DECISION_CONTINUE, "stall_count": 0}
    stalled = stall_count + 1
    if stalled >= stall_k:
        return {"decision": DECISION_REGENERATE, "stall_count": stalled}
    return {"decision": DECISION_CONTINUE, "stall_count": stalled}


def run_collapse_loop(
    goal_vec: list[float],
    states: list[list[float]],
    predicates_check: Callable[[int], bool],
    *,
    horizon: int,
    tau: float = DEFAULT_TAU,
    epsilon: float = DEFAULT_EPSILON,
    stall_k: int = DEFAULT_STALL_K,
) -> dict[str, Any]:
    """Drive route_step over a state trajectory — the P1 skeleton, unit-testable."""
    prev = 0.0
    stall = 0
    trace: list[dict[str, Any]] = []
    for step, state in enumerate(states, start=1):
        score = collapse_score(state, goal_vec)
        out = route_step(
            collapse=score,
            prev_collapse=prev,
            predicates_pass=predicates_check(step),
            step_count=step,
            horizon=horizon,
            tau=tau,
            epsilon=epsilon,
            stall_count=stall,
            stall_k=stall_k,
        )
        trace.append({"step": step, "collapse": round(score, 6), **out})
        stall = out["stall_count"]
        prev = score
        if out["decision"] in (DECISION_TERMINATE, DECISION_AUDIT_FAIL, DECISION_REGENERATE, DECISION_FORCE_AUDIT):
            return {"final": out["decision"], "steps": step, "trace": trace}
    return {"final": DECISION_FORCE_AUDIT, "steps": len(states), "trace": trace}


# --- P1: EXECUTE + AUDIT ------------------------------------------------------

VERDICT_PASS = "pass"
VERDICT_FAIL = "fail"
VERDICT_FALSE_PASS = "false_pass_caught"


async def execute_cfsm(
    ir: Mapping[str, Any],
    body: Mapping[str, Any] | None = None,
    *,
    predicates: list[dict[str, Any]] | None = None,
    tau: float = DEFAULT_TAU,
) -> dict[str, Any]:
    """COMPILE → EXECUTE on dag_runner → AUDIT (collapse + predicates).

    Predicates decide pass/fail — collapse ≥ τ with failing predicates is the
    goal-lie the audit exists to catch, and it is labeled as such.
    """
    from CortexOS.execution.race_router import eval_predicates  # lazy: avoids cycle

    compiled = compile_ir(ir)
    if not compiled["ok"]:
        return {"ok": False, "stage": "compile", "verdict": None, "errors": compiled["errors"]}
    program = compiled["program"]

    goal = str(ir.get("goal") or "")
    run_body = dict(body or {})
    prompt = str(run_body.get("prompt") or goal)
    run_id = str(run_body.get("session_id") or ("cfsm-" + uuid.uuid4().hex[:8]))
    context = ExecutionContext(
        run_id,
        {
            "prompt": prompt,
            "query": prompt,
            "params": run_body.get("params") or {},
            "rag_corpus": run_body.get("rag_corpus") or [],
            "actor": run_body.get("actor"),
        },
    )
    try:
        result = await run_dag(program, context, ModelRouter(), CostLedger())
    except Exception as exc:
        return {"ok": False, "stage": "execute", "verdict": None, "errors": [f"execute:{exc}"]}

    node = result.outputs.get(program.output_node_id)
    output = node.output if node is not None else None

    preds = list(predicates) if predicates is not None else list(ir.get("predicates") or [])
    predicates_pass = eval_predicates(output, preds) if preds else True
    collapse = collapse_score(scoreboard.embed_goal(str(output)), scoreboard.embed_goal(goal or prompt))

    if predicates_pass:
        verdict = VERDICT_PASS
    elif collapse >= tau:
        verdict = VERDICT_FALSE_PASS
    else:
        verdict = VERDICT_FAIL

    return {
        "ok": predicates_pass,
        "stage": "audit",
        "verdict": verdict,
        "collapse": round(collapse, 6),
        "predicates_pass": predicates_pass,
        "output": output,
        "run_id": run_id,
        "horizon": ir.get("horizon"),
        "node_count": len(ir.get("nodes") or []),
        "errors": [],
    }


async def iterate_cfsm(
    goal: str,
    body: Mapping[str, Any] | None = None,
    *,
    predicates: list[dict[str, Any]] | None = None,
    horizons: tuple[int, ...] = ALLOWED_HORIZONS,
    tau: float = DEFAULT_TAU,
    record: bool = True,
) -> dict[str, Any]:
    """The regenerate loop: GENERATE → EXECUTE+AUDIT, escalating horizon 3→5→7.

    Every attempt lands on the scoreboard as preset ``gen_cfsm`` so the racing
    prior learns when generated FSMs beat the fixed presets.
    """
    attempts: list[dict[str, Any]] = []
    task_family = scoreboard.family_id(goal)
    for horizon in horizons:
        ir = generate_ir(goal, horizon)
        started = time.perf_counter()
        report = await execute_cfsm(ir, body, predicates=predicates, tau=tau)
        report["latency_ms"] = int((time.perf_counter() - started) * 1000)
        attempts.append(report)
        if record:
            scoreboard.init()
            scoreboard.record_run(
                str(report.get("run_id") or f"cfsm-h{horizon}"),
                task_family,
                "gen_cfsm",
                mode="scaled",
                score=1.0 if report.get("ok") else 0.0,
                predicates_pass=report.get("predicates_pass"),
                latency_ms=int(report.get("latency_ms") or 0),
            )
        if report.get("ok"):
            if record:
                scoreboard.upsert_family(task_family, scoreboard.embed_goal(goal))
            return {
                "ok": True,
                "goal": goal,
                "family": task_family,
                "final": report,
                "attempts": attempts,
                "regenerations": len(attempts) - 1,
            }
    return {
        "ok": False,
        "goal": goal,
        "family": task_family,
        "final": attempts[-1] if attempts else None,
        "attempts": attempts,
        "regenerations": max(len(attempts) - 1, 0),
    }
