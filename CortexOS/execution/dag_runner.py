"""Execute parsed Agentic DSL DAGs with routing + cost controls."""

from __future__ import annotations

import math
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from netie.execution.errors import (
    OutboundNotSendable,
    UnsupportedDAGNodeKind,
    WorkflowCostCeilingExceeded,
)
from netie.execution.executor import invoke_routed_completion
from netie.execution.model_router import ModelRequest, ModelRouter
from netie.fabrication.dag_compiler import DAGCompiler
from netie.fabrication.dsl_parser import AgenticDSLProgram, DSLNode, NodeType
from netie.personality.timing import is_sendable_now
from netie.routing.adapters.base import AdapterRequest
from netie.routing.cost_ledger import CostLedger
from netie.routing.tiers import Tier
from netie.routing.token_estimate import estimate_prompt_tokens

_PLACEHOLDER = re.compile(r"\{([\w\-]+)\}")


class ExecutionContext(Mapping[str, Any]):
    """Mutable bag keyed by node id (outputs) plus run metadata."""

    def __init__(self, run_id: str, seed: Mapping[str, Any] | None = None) -> None:
        self.run_id = run_id
        self._data: dict[str, Any] = dict(seed or {})

    def __getitem__(self, key: str) -> Any:
        return self._data[key]

    def __iter__(self):
        return iter(self._data)

    def __len__(self) -> int:
        return len(self._data)

    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, default)

    def update_with_node(self, node_id: str, result: NodeResult) -> None:
        self._data[node_id] = result.output

    def set_default(self, key: str, value: Any) -> None:
        """Seed run-scoped machinery (tool broker, progress sink) without
        clobbering a value the caller deliberately supplied."""
        self._data.setdefault(key, value)


@dataclass(slots=True)
class NodeResult:
    node_id: str
    output: Any
    tier: str
    cost_myr: float


@dataclass(slots=True)
class DAGResult:
    outputs: dict[str, NodeResult] = field(default_factory=dict)


def tier_pair(node: DSLNode) -> tuple[Tier, Tier]:
    if node.default_tier is None or node.max_tier is None:
        raise ValueError(f"Node {node.id!r}: llm_judged requires default_tier and max_tier")
    return Tier(node.default_tier.value), Tier(node.max_tier.value)


def render_prompt(template: str | None, ctx: ExecutionContext, node: DSLNode) -> str:
    """Render `{upstream_id}` from context outputs; concatenate inputs when template is absent."""
    if template:
        def repl(m: re.Match[str]) -> str:
            key = m.group(1)
            val = ctx.get(key, "")
            if isinstance(val, dict) and "content" in val:
                return str(val["content"])
            return str(val)

        return _PLACEHOLDER.sub(repl, template)

    parts: list[str] = []
    for inp_id in node.inputs:
        val = ctx.get(inp_id, "")
        if isinstance(val, dict) and "content" in val:
            parts.append(str(val["content"]))
        else:
            parts.append(str(val))
    return "\n".join(parts) if parts else ""


def _serializable_output(raw: Any) -> Any:
    if isinstance(raw, dict):
        return dict(raw)
    return raw


def _journal_enabled() -> bool:
    import os

    return os.environ.get("CORTEX_STEP_JOURNAL", "1").strip().lower() not in {
        "0",
        "false",
        "off",
        "no",
    }


def _journal_step_key(node: DSLNode) -> str:
    """Content-addressed key for one node's work.

    Keyed on node identity plus the prompt *template* rather than the rendered
    prompt: rendering pulls in upstream output, and on a resume that upstream is
    itself replayed, so template + identity is both stable and collision-free
    across a fan-out (each fan item carries its own label/item annotation).
    """
    from netie.execution.step_journal import step_key

    ann = node.annotations if isinstance(node.annotations, dict) else {}
    return step_key(
        node.prompt or "",
        {
            "node": node.id,
            "type": str(node.type),
            "inputs": list(node.inputs or []),
            "label": ann.get("label"),
            "phase": ann.get("phase"),
            "item": ann.get("item") or ann.get("fan_item"),
        },
    )


async def execute_llm_judged_node(
    node: DSLNode,
    context: ExecutionContext,
    router: ModelRouter,
    ledger: CostLedger,
    *,
    workflow_cost_ceiling_myr: float,
) -> NodeResult:
    prompt = render_prompt(node.prompt, context, node)
    dt, mt = tier_pair(node)

    ceiling_node = node.cost_ceiling_myr
    wf = workflow_cost_ceiling_myr if math.isfinite(workflow_cost_ceiling_myr) else math.inf
    resolved_ceiling = wf if ceiling_node is None else min(wf, ceiling_node)

    model_req = ModelRequest(
        request_type=(node.request_type or node.id),
        prompt=prompt,
        default_tier=dt,
        max_tier=mt,
        cost_ceiling_myr=resolved_ceiling if resolved_ceiling is not math.inf else (ceiling_node or 1e9),
        provider=node.provider,
        metadata={"is_vip": bool(context.get("is_vip", False))},
    )
    mtoks = node.max_tokens if node.max_tokens is not None else 1000
    adapter_req = AdapterRequest(
        model="",
        system=node.system or "",
        prompt=prompt,
        max_tokens=mtoks,
    )
    outcome = await invoke_routed_completion(
        router,
        ledger,
        run_id=context.run_id,
        workflow_cost_ceiling_myr=workflow_cost_ceiling_myr,
        node_id=node.id,
        model_req=model_req,
        adapter_req=adapter_req,
        node_cost_ceiling_myr=node.cost_ceiling_myr,
    )
    resp = outcome.response
    out = {"content": resp.content, "raw": resp.raw}
    return NodeResult(
        node_id=node.id, output=out, tier=outcome.tier, cost_myr=float(outcome.cost_myr)
    )


async def execute_deterministic_rule_node(node: DSLNode, context: ExecutionContext) -> NodeResult:
    from netie.compliance.engine import ComplianceEngine

    if not node.ruleset:
        raise ValueError(f"Node {node.id!r}: deterministic_rule requires ruleset")
    if not node.inputs:
        raise ValueError(f"Node {node.id!r}: deterministic_rule requires at least one input")
    doc = context.get(node.inputs[0])
    engine = ComplianceEngine.from_ruleset(node.ruleset)
    violations = engine.check(doc if isinstance(doc, dict) else None)
    return NodeResult(
        node_id=node.id,
        output={"violations": violations, "passed": len(violations) == 0},
        tier="deterministic",
        cost_myr=0.0,
    )


_AGENT_EFFORT_TIERS = {"low": ("T0", "T1"), "medium": ("T1", "T2"), "high": ("T2", "T3")}


def _agent_tier_pair(node: DSLNode) -> tuple[Tier, Tier]:
    ann = node.annotations if isinstance(node.annotations, dict) else {}
    dt, mt = _AGENT_EFFORT_TIERS.get(str(ann.get("effort") or "medium").lower(), ("T1", "T2"))
    return Tier(dt), Tier(mt)


def _price_one_call(
    node: DSLNode,
    router: ModelRouter,
    context: ExecutionContext,
    prompt: str,
    tiers: tuple[Tier, Tier],
) -> float:
    dt, mt = tiers
    model_req = ModelRequest(
        request_type=(node.request_type or node.id),
        prompt=prompt,
        default_tier=dt,
        max_tier=mt,
        cost_ceiling_myr=node.cost_ceiling_myr or 1e9,
        provider=node.provider,
        metadata={"is_vip": bool(context.get("is_vip", False))},
    )
    routed = router.route(model_req)
    blob = f"{node.system or ''}\n{prompt}"
    from netie.execution.executor import adapter_token_estimate_family

    est_prompt = estimate_prompt_tokens(
        blob,
        family=adapter_token_estimate_family(routed.provider),
    )
    mtoks = node.max_tokens if node.max_tokens is not None else 1000
    return float(routed.adapter.cost_myr(est_prompt, mtoks))


def estimate_node_cost(
    node: DSLNode,
    router: ModelRouter,
    context: ExecutionContext,
) -> float:
    if node.type == NodeType.LLM_JUDGED:
        prompt = render_prompt(node.prompt, context, node)
        return _price_one_call(node, router, context, prompt, tier_pair(node))
    if node.type == NodeType.AGENT_TASK:
        # An agent task may loop; price the whole step allowance so the ceiling
        # gates the loop rather than only its first call.
        prompt = render_prompt(node.prompt, context, node)
        ann = node.annotations if isinstance(node.annotations, dict) else {}
        steps = max(1, int(ann.get("max_steps") or 4))
        return steps * _price_one_call(node, router, context, prompt, _agent_tier_pair(node))
    return 0.0


async def execute_node(
    node: DSLNode,
    context: ExecutionContext,
    router: ModelRouter,
    ledger: CostLedger,
    *,
    workflow_cost_ceiling_myr: float,
) -> NodeResult:
    if node.outbound:
        tz = str(context.get("user_tz") or "Asia/Kuala_Lumpur")
        religion = context.get("user_religion")
        urgency = str(context.get("message_urgency") or "normal")
        if not is_sendable_now(tz, religion, urgency):
            raise OutboundNotSendable(
                node.id,
                "outside send window (quiet hours or Friday prayer); use message_urgency=urgent to bypass.",
            )
    if node.type == NodeType.LLM_JUDGED:
        return await execute_llm_judged_node(
            node, context, router, ledger, workflow_cost_ceiling_myr=workflow_cost_ceiling_myr
        )
    if node.type == NodeType.DETERMINISTIC_RULE:
        return await execute_deterministic_rule_node(node, context)
    if node.type == NodeType.DOCUMENT_REF:
        key = node.context_key or "document"
        payload = context.get(key)
        return NodeResult(node_id=node.id, output=payload, tier="deterministic", cost_myr=0.0)
    if node.type == NodeType.EMIT:
        merged: dict[str, Any] = {}
        for i in node.inputs:
            merged[i] = _serializable_output(context.get(i))
        return NodeResult(node_id=node.id, output=merged, tier="emit", cost_myr=0.0)
    if node.type == NodeType.TOOL_CALL:
        return await execute_tool_call_node(node, context)
    if node.type == NodeType.RAG_RETRIEVE:
        return await execute_rag_retrieve_node(node, context)
    if node.type == NodeType.RAG_RERANK:
        return await execute_rag_rerank_node(node, context)
    if node.type == NodeType.RAG_ANSWER:
        return await execute_rag_answer_node(node, context)
    if node.type == NodeType.A2A_CALL:
        return await execute_a2a_call_node(node, context)
    if node.type == NodeType.AGENT_TASK:
        return await execute_agent_task_node(
            node, context, router, ledger, workflow_cost_ceiling_myr=workflow_cost_ceiling_myr
        )
    raise UnsupportedDAGNodeKind(node.id, node.type)


async def execute_agent_task_node(
    node: DSLNode,
    context: ExecutionContext,
    router: ModelRouter,
    ledger: CostLedger,
    *,
    workflow_cost_ceiling_myr: float,
) -> NodeResult:
    """One workflow subagent — preset prompt plus a bounded, allowlisted tool loop."""
    from netie.execution.agent_task import run_agent_task

    output, telemetry = await run_agent_task(
        node, context, router, ledger, workflow_cost_ceiling_myr=workflow_cost_ceiling_myr
    )
    return NodeResult(
        node_id=node.id,
        output=output,
        tier=telemetry.tier or "agent",
        cost_myr=telemetry.cost_myr,
    )


async def execute_rag_retrieve_node(node: DSLNode, context: ExecutionContext) -> NodeResult:
    from CortexOS.rag import lexical

    query = str(context.get("query") or context.get("prompt") or "")
    # Corrective round: fold prior answer text into the query
    ann = node.annotations if isinstance(node.annotations, dict) else {}
    if ann.get("corrective"):
        for inp in node.inputs:
            prior = context.get(inp)
            if isinstance(prior, dict) and prior.get("answer"):
                query = f"{query} {prior.get('answer')}"
                break
    corpus = context.get("rag_corpus") or context.get("corpus") or []
    if not isinstance(corpus, list):
        corpus = []
    top_k = int(ann.get("top_k") or 8)
    hits = lexical.retrieve(query, corpus, top_k=top_k)
    return NodeResult(
        node_id=node.id,
        output={"query": query, "hits": hits, "depth": ann.get("depth"), "round": ann.get("round", 1)},
        tier="rag",
        cost_myr=0.0,
    )


async def execute_rag_rerank_node(node: DSLNode, context: ExecutionContext) -> NodeResult:
    from CortexOS.rag import lexical

    query = str(context.get("query") or context.get("prompt") or "")
    hits: list = []
    for inp in node.inputs:
        val = context.get(inp)
        if isinstance(val, dict) and isinstance(val.get("hits"), list):
            hits = list(val["hits"])
            query = str(val.get("query") or query)
            break
    ann = node.annotations if isinstance(node.annotations, dict) else {}
    top_n = int(ann.get("top_n") or 10)
    ranked = lexical.rerank(query, hits, top_n=top_n)
    return NodeResult(
        node_id=node.id,
        output={"query": query, "hits": ranked, "reranked": True},
        tier="rag",
        cost_myr=0.0,
    )


async def execute_rag_answer_node(node: DSLNode, context: ExecutionContext) -> NodeResult:
    from CortexOS.rag import lexical

    query = str(context.get("query") or context.get("prompt") or "")
    hits: list = []
    prior_answer = None
    for inp in node.inputs:
        val = context.get(inp)
        if not isinstance(val, dict):
            continue
        if isinstance(val.get("hits"), list):
            hits = list(val["hits"])
            query = str(val.get("query") or query)
        if val.get("answer"):
            prior_answer = val
    out = lexical.answer_from_hits(query, hits)
    out["coverage"] = lexical.coverage(hits)
    if prior_answer and prior_answer.get("answer"):
        out["rounds"] = 2
        out["prior"] = {"answer": prior_answer.get("answer"), "citations": prior_answer.get("citations")}
    else:
        out["rounds"] = 1
    # Optional ledger breadcrumb (read-path metadata) when actor present
    actor = context.get("actor")
    if actor:
        try:
            from packs.dms.audit.ledger import append as ledger_append

            ledger_append(
                str(actor),
                "rag.answer",
                {"run_id": context.run_id, "n_hits": out.get("n_hits"), "rounds": out.get("rounds")},
            )
        except Exception:
            pass
    return NodeResult(node_id=node.id, output=out, tier="rag", cost_myr=0.0)


async def execute_a2a_call_node(node: DSLNode, context: ExecutionContext) -> NodeResult:
    import uuid as _uuid
    from datetime import datetime, timezone

    from CortexOS.a2a.protocol import A2AMessage
    from CortexOS.a2a.transport_ws import InProcessA2ATransport

    transport = context.get("_a2a_transport")
    if transport is None:
        transport = InProcessA2ATransport()
    ann = node.annotations if isinstance(node.annotations, dict) else {}
    target = node.target_did or ann.get("target_did") or context.get("target_did")
    payload: dict[str, Any] = {}
    for inp in node.inputs:
        val = context.get(inp)
        if isinstance(val, dict):
            payload.update(val)
        elif val is not None:
            payload[inp] = val
    if isinstance(ann.get("payload"), dict):
        payload.update(ann["payload"])
    if not target:
        return NodeResult(
            node_id=node.id,
            output={"ok": False, "error": "missing target_did"},
            tier="a2a",
            cost_myr=0.0,
        )
    msg = A2AMessage(
        id=str(_uuid.uuid4()),
        from_did=str(context.get("from_did") or "cortex:self"),
        to=str(target),
        created_at=datetime.now(timezone.utc),
        type=str(ann.get("intent") or node.capability or "https://netie.com/a2a/v1/message"),
        body=payload,
        expects_reply=True,
    )
    reply = transport.send(msg)
    if reply is None:
        return NodeResult(
            node_id=node.id,
            output={"ok": False, "error": "unknown_agent", "to": msg.to},
            tier="a2a",
            cost_myr=0.0,
        )
    return NodeResult(
        node_id=node.id,
        output={"ok": True, "reply": reply.model_dump(mode="json")},
        tier="a2a",
        cost_myr=0.0,
    )


async def execute_tool_call_node(node: DSLNode, context: ExecutionContext) -> NodeResult:
    """F8 — sandboxed TOOL_CALL via host tool_runner (allowlist + ledger)."""
    from netie.execution.tool_runner import ToolCallError, run_tool_call

    params: dict[str, Any] = {}
    for inp_id in node.inputs:
        val = context.get(inp_id)
        if isinstance(val, dict):
            params.update(val)
        elif val is not None:
            params[inp_id] = val
    ann_params = node.annotations.get("params") if isinstance(node.annotations, dict) else None
    if isinstance(ann_params, dict):
        params.update(ann_params)

    actor = str(context.get("actor") or "system")
    tool = node.tool_name or ""
    try:
        result = run_tool_call(tool, params, actor=actor, run_id=context.run_id)
    except ToolCallError as exc:
        return NodeResult(
            node_id=node.id,
            output={"ok": False, "error": str(exc), "verdict": exc.verdict, **exc.detail},
            tier="tool",
            cost_myr=0.0,
        )
    return NodeResult(node_id=node.id, output=result, tier="tool", cost_myr=0.0)


def _execution_layers(program: AgenticDSLProgram) -> list[list[DSLNode]]:
    """Topological layers — nodes within a layer have no dependency on each other."""
    comp = DAGCompiler(dead_code_eliminator=False)
    from netie.result import Ok

    c = comp.compile(program)
    assert isinstance(c, Ok)
    by_id = {n.id: n for n in program.nodes}
    layers: list[list[DSLNode]] = []
    seen: set[str] = set()
    for layer in c.value.execution_order:
        batch = [by_id[nid] for nid in layer if nid not in seen]
        seen.update(n.id for n in batch)
        if batch:
            layers.append(batch)
    return layers


def _flatten_execution_order(program: AgenticDSLProgram) -> list[DSLNode]:
    return [node for layer in _execution_layers(program) for node in layer]


def _gate_cost(
    node: DSLNode,
    context: ExecutionContext,
    router: ModelRouter,
    ledger: CostLedger,
    wf: float,
) -> None:
    projected = estimate_node_cost(node, router, context)
    if not ledger.enforce_ceiling(context.run_id, wf, projected_additional_myr=projected):
        raise WorkflowCostCeilingExceeded(
            f"Workflow {context.run_id}: projected spend would breach {wf} MYR ceiling"
        )


async def run_dag(
    dag: AgenticDSLProgram,
    context: ExecutionContext,
    router: ModelRouter,
    ledger: CostLedger,
    workflow_cost_ceiling_myr: float | None = None,
    *,
    parallel: bool = False,
    max_parallel: int | None = None,
    should_abort: Any = None,
    on_event: Any = None,
    resume: bool = False,
) -> DAGResult:
    """Execute the DAG in topological order.

    ``parallel=True`` runs each topological layer concurrently — sibling nodes
    in a layer have no dependency on each other by construction, which is what
    makes a workflow's fan-out phase finish in the time of its slowest agent
    rather than the sum of all of them. Sequential remains the default so
    existing callers keep their ordering guarantees.

    ``max_parallel`` caps concurrent agents in a layer (OOM / SQLite safety).
    ``should_abort`` is called before each layer; truthy aborts the remaining DAG.
    ``on_event`` receives progress dicts (``node_start`` / ``node_done`` /
    ``node_error``) and is also handed to agent nodes for step-level telemetry.

    ``resume=True`` replays nodes this ``run_id`` already completed from the step
    journal instead of re-running them. Recording is unconditional; replay is
    not, because a run_id is only unique by convention — a caller that reuses one
    would otherwise silently inherit a previous process's results.
    """
    import asyncio

    from netie.execution import step_journal

    wf = workflow_cost_ceiling_myr
    wf_val = wf if wf is not None else math.inf
    journal_on = _journal_enabled()
    result = DAGResult()
    emit = on_event if callable(on_event) else None
    if emit is not None:
        context.set_default("_on_event", emit)
    abort = should_abort if callable(should_abort) else None
    cap = max(1, int(max_parallel)) if max_parallel else None

    async def _one(node: DSLNode) -> tuple[DSLNode, NodeResult]:
        if emit is not None:
            emit({"type": "node_start", "node": node.id, "kind": str(node.type), **_node_meta(node)})

        # Content-addressed replay: a run resumed under its original run_id skips
        # the nodes it already finished. Journal faults must never fail a run.
        jkey = ""
        cached = None
        if journal_on:
            try:
                jkey = _journal_step_key(node)
                cached = step_journal.get_cached(context.run_id, jkey) if resume else None
            except Exception:
                jkey, cached = "", None
            if isinstance(cached, dict):
                nr = NodeResult(
                    node_id=node.id,
                    output=cached.get("output"),
                    tier=str(cached.get("tier") or "cached"),
                    cost_myr=0.0,  # replay spends nothing; original cost is in the journal
                )
                if emit is not None:
                    emit(
                        {
                            "type": "node_done",
                            "node": node.id,
                            "tier": nr.tier,
                            "cost_myr": 0.0,
                            "replayed": True,
                            "cached_cost_myr": float(cached.get("cost_myr") or 0.0),
                            **_node_meta(node),
                        }
                    )
                return node, nr

        try:
            nr = await execute_node(node, context, router, ledger, workflow_cost_ceiling_myr=wf_val)
        except Exception as exc:
            if emit is not None:
                emit({"type": "node_error", "node": node.id, "error": str(exc)[:300], **_node_meta(node)})
            raise
        if jkey:
            try:
                step_journal.put_cached(
                    context.run_id,
                    jkey,
                    {
                        "output": _serializable_output(nr.output),
                        "tier": nr.tier,
                        "cost_myr": float(nr.cost_myr),
                    },
                    node_id=node.id,
                )
            except Exception:
                pass
        if emit is not None:
            out = nr.output if isinstance(nr.output, dict) else {}
            emit(
                {
                    "type": "node_done",
                    "node": node.id,
                    "tier": nr.tier,
                    "cost_myr": nr.cost_myr,
                    "telemetry": out.get("telemetry"),
                    "excerpt": str(out.get("content") or "")[:2000],
                    **_node_meta(node),
                }
            )
        return node, nr

    for layer in _execution_layers(dag):
        if abort and abort():
            break
        if wf is not None:
            for node in layer:
                _gate_cost(node, context, router, ledger, wf)
        if parallel and len(layer) > 1:
            batches = [layer]
            if cap and len(layer) > cap:
                batches = [layer[i : i + cap] for i in range(0, len(layer), cap)]
            for batch in batches:
                if abort and abort():
                    break
                for node, nr in await asyncio.gather(*(_one(n) for n in batch)):
                    context.update_with_node(node.id, nr)
                    result.outputs[node.id] = nr
        else:
            for node in layer:
                if abort and abort():
                    break
                _, nr = await _one(node)
                context.update_with_node(node.id, nr)
                result.outputs[node.id] = nr
    return result


def _node_meta(node: DSLNode) -> dict[str, Any]:
    ann = node.annotations if isinstance(node.annotations, dict) else {}
    return {
        "phase": ann.get("phase"),
        "phase_title": ann.get("phase_title"),
        "label": ann.get("label"),
        "purpose": ann.get("purpose"),
    }
