"""Execute parsed Agentic DSL DAGs with routing + cost controls."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Any, Mapping

from netie.execution.errors import OutboundNotSendable, UnsupportedDAGNodeKind, WorkflowCostCeilingExceeded
from netie.execution.executor import invoke_routed_completion
from netie.execution.model_router import ModelRequest, ModelRouter
from netie.fabrication.dag_compiler import DAGCompiler
from netie.fabrication.dsl_parser import AgenticDSLProgram, DSLNode, NodeType
from netie.routing.adapters.base import AdapterRequest
from netie.personality.timing import is_sendable_now
from netie.routing.cost_ledger import CostLedger
from netie.routing.token_estimate import estimate_prompt_tokens
from netie.routing.tiers import Tier

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

    def update_with_node(self, node_id: str, result: "NodeResult") -> None:
        self._data[node_id] = result.output


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


def estimate_node_cost(
    node: DSLNode,
    router: ModelRouter,
    context: ExecutionContext,
) -> float:
    if node.type != NodeType.LLM_JUDGED:
        return 0.0
    prompt = render_prompt(node.prompt, context, node)
    dt, mt = tier_pair(node)
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
    raise UnsupportedDAGNodeKind(node.id, node.type)


def _flatten_execution_order(program: AgenticDSLProgram) -> list[DSLNode]:
    comp = DAGCompiler(dead_code_eliminator=False)
    from netie.result import Ok

    c = comp.compile(program)
    assert isinstance(c, Ok)
    layered = c.value.execution_order
    by_id = {n.id: n for n in program.nodes}
    out: list[DSLNode] = []
    seen: set[str] = set()
    for layer in layered:
        for nid in layer:
            if nid not in seen:
                seen.add(nid)
                out.append(by_id[nid])
    return out


async def run_dag(
    dag: AgenticDSLProgram,
    context: ExecutionContext,
    router: ModelRouter,
    ledger: CostLedger,
    workflow_cost_ceiling_myr: float | None = None,
) -> DAGResult:
    wf = workflow_cost_ceiling_myr
    wf_val = wf if wf is not None else math.inf
    order = _flatten_execution_order(dag)
    result = DAGResult()
    for node in order:
        if wf is not None:
            projected = estimate_node_cost(node, router, context)
            if not ledger.enforce_ceiling(
                context.run_id,
                wf,
                projected_additional_myr=projected,
            ):
                raise WorkflowCostCeilingExceeded(
                    f"Workflow {context.run_id}: projected spend would breach "
                    f"{wf} MYR ceiling"
                )
        nr = await execute_node(
            node, context, router, ledger, workflow_cost_ceiling_myr=wf_val
        )
        context.update_with_node(node.id, nr)
        result.outputs[node.id] = nr
    return result
