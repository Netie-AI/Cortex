"""AGENT_TASK node — one workflow subagent: preset prompt + bounded tool loop.

This is a *node kind* on the existing DAG runner, not a new orchestrator. The
loop is deliberately small: call the model, and if it answers with a tool call
from its own allowlist, run it and feed the observation back. It stops at
``max_steps`` and returns whatever it has, because a subagent that runs forever
starves the phase it belongs to.

Tool access is per-node. A node's ``annotations["tools"]`` is the whole
allowlist — a research agent cannot touch the filesystem and a code auditor
cannot reach the network unless its template said so.
"""

from __future__ import annotations

import json
import math
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from CortexOS.execution.executor import invoke_routed_completion
from CortexOS.execution.model_router import ModelRequest, ModelRouter
from CortexOS.fabrication.dsl_parser import DSLNode
from CortexOS.routing.adapters.base import AdapterRequest
from CortexOS.routing.cost_ledger import CostLedger
from CortexOS.routing.tiers import Tier

#: (tool_name, params) -> result dict. Injected via ``context["_tool_broker"]``.
ToolBroker = Callable[[str, dict], dict]

DEFAULT_MAX_STEPS = 4
_EFFORT_TIERS: dict[str, tuple[str, str]] = {
    "low": ("T0", "T1"),
    "medium": ("T1", "T2"),
    "high": ("T2", "T3"),
}


@dataclass(slots=True)
class ToolCallRecord:
    tool: str
    ok: bool
    ms: int
    summary: str


@dataclass(slots=True)
class AgentTaskTelemetry:
    """What the Background tasks panel renders for one agent."""

    label: str
    purpose: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    steps: int = 0
    tool_calls: list[ToolCallRecord] = field(default_factory=list)
    elapsed_ms: int = 0
    model: str = ""
    tier: str = ""
    cost_myr: float = 0.0
    error: str = ""
    stop_reason: str = ""
    quality_passed: bool | None = None

    @property
    def tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    def as_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "purpose": self.purpose,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "tokens": self.tokens,
            "steps": self.steps,
            "tools": [
                {"tool": t.tool, "ok": t.ok, "ms": t.ms, "summary": t.summary}
                for t in self.tool_calls
            ],
            "tool_count": len(self.tool_calls),
            "elapsed_ms": self.elapsed_ms,
            "model": self.model,
            "tier": self.tier,
            "cost_myr": round(self.cost_myr, 6),
            "error": self.error,
            "stop_reason": self.stop_reason,
            "quality_passed": self.quality_passed,
        }


def default_broker(name: str, params: dict) -> dict:
    """Cortex's own tools. Web tools are built in; everything else goes through
    the governed F8 runner so an agent cannot reach an unregistered action."""
    from CortexOS.execution import web_tools

    params = dict(params or {})
    if name == "web_search" and not str(params.get("query") or "").strip():
        params["query"] = str(params.pop("q", None) or params.pop("search", None) or params.pop("term", None) or "")
    if name == "web_fetch" and not str(params.get("url") or "").strip():
        params["url"] = str(params.pop("uri", None) or params.pop("link", None) or "")

    fn = web_tools.WEB_TOOLS.get(name)
    if fn is not None:
        return fn(**params)

    from CortexOS.discovery.find import DISCOVERY_TOOLS

    disc = DISCOVERY_TOOLS.get(name)
    if disc is not None:
        return disc(**(params or {}))

    from CortexOS.execution.tool_runner import ToolCallError, run_tool_call

    try:
        return run_tool_call(name, params, actor="workflow", run_id="workflow")
    except ToolCallError as exc:
        return {"ok": False, "error": str(exc), "verdict": exc.verdict}


_OPENAI_TOOL_SPECS: dict[str, dict[str, Any]] = {
    "web_search": {
        "description": "Search the web. Returns ranked title, url, snippet.",
        "properties": {
            "query": {"type": "string"},
            "max_results": {"type": "integer"},
        },
        "required": ["query"],
    },
    "web_fetch": {
        "description": "Fetch one http(s) URL and return readable text.",
        "properties": {
            "url": {"type": "string"},
            "max_chars": {"type": "integer"},
        },
        "required": ["url"],
    },
}


def openai_tools(names: list[str]) -> list[dict[str, Any]]:
    """OpenAI-style tool schemas for OpenVault / chat.completions."""
    out: list[dict[str, Any]] = []
    for name in names:
        spec = _OPENAI_TOOL_SPECS.get(name) or {
            "description": name,
            "properties": {},
            "required": [],
        }
        out.append(
            {
                "type": "function",
                "function": {
                    "name": name,
                    "description": spec["description"],
                    "parameters": {
                        "type": "object",
                        "properties": spec["properties"],
                        "required": spec["required"],
                    },
                },
            }
        )
    return out


def _tool_instructions(tools: list[str]) -> str:
    if not tools:
        return ""
    from CortexOS.discovery.find import SCHEMAS as DISCOVERY_SCHEMAS
    from CortexOS.execution import web_tools

    lines = [f"You may call these tools: {', '.join(tools)}"]
    for schema in list(web_tools.SCHEMAS) + list(DISCOVERY_SCHEMAS):
        if schema["name"] in tools:
            lines.append(f"  {schema['name']}: {schema['description']} params={schema['params']}")
    lines.append(
        'Prefer a native tool call. Else reply with ONLY this JSON: {"tool":"NAME","params":{...}}\n'
        "You will get the result and may call another. When you are finished, "
        "reply with your answer as plain text and no JSON."
    )
    return "\n".join(lines)


def _quality_criteria(ann: Mapping[str, Any]) -> list[str]:
    raw = ann.get("quality_criteria")
    if not isinstance(raw, (list, tuple)):
        return []
    return [str(c).strip() for c in raw if str(c).strip()]


async def _independent_quality_check(
    content: str,
    criteria: list[str],
    context: Any,
    *,
    step: int,
):
    """Grade a final answer with a verifier that is not the generator (R-0003 / F20b).

    Callers bind ``context["_quality_verify"]``. No bound verifier is a fail-closed
    refuse, never a rubber-stamp pass. The callable may be sync or async.
    """
    from CortexOS.execution.generator_verifier import parse_verifier_payload

    verify = context.get("_quality_verify") if hasattr(context, "get") else None
    if not callable(verify):
        return parse_verifier_payload(
            {
                "passed": False,
                "feedback": "no independent verifier bound; refusing rubber-stamp",
            },
            criteria=criteria,
        )
    raw = verify(content, criteria, step)
    if hasattr(raw, "__await__"):
        raw = await raw
    return parse_verifier_payload(raw, criteria=criteria)


def _parse_tool_call(text: str, allowed: list[str]) -> dict | None:
    """First brace-balanced object containing a "tool" key, if it is allowed."""
    start = text.find("{")
    while start >= 0:
        depth = 0
        in_str = False
        esc = False
        for i in range(start, len(text)):
            ch = text[i]
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
                continue
            if ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    try:
                        obj = json.loads(text[start : i + 1])
                    except json.JSONDecodeError:
                        break
                    tool = obj.get("tool") if isinstance(obj, dict) else None
                    if isinstance(tool, str) and tool in allowed:
                        params = obj.get("params")
                        return {"tool": tool, "params": params if isinstance(params, dict) else {}}
                    break
        start = text.find("{", start + 1)
    return None


def native_tool_call(raw: Any, allowed: list[str]) -> dict | None:
    """First OpenAI-style tool_call on a chat.completions payload, if allowed."""
    if not isinstance(raw, dict) or not allowed:
        return None
    choices = raw.get("choices") or []
    if not choices or not isinstance(choices[0], dict):
        return None
    message = choices[0].get("message") or {}
    if not isinstance(message, dict):
        return None
    for tc in message.get("tool_calls") or []:
        if not isinstance(tc, dict):
            continue
        fn = tc.get("function") or {}
        if not isinstance(fn, dict):
            continue
        name = str(fn.get("name") or "")
        if name not in allowed:
            continue
        args = fn.get("arguments")
        if isinstance(args, str):
            try:
                args = json.loads(args) if args.strip() else {}
            except json.JSONDecodeError:
                args = {}
        if not isinstance(args, dict):
            args = {}
        return {"tool": name, "params": args}
    return None


def _upstream_blob(node: DSLNode, context: Mapping[str, Any], *, limit: int = 6000) -> str:
    parts: list[str] = []
    for inp in node.inputs:
        val = context.get(inp)
        if val in (None, "", {}):
            continue
        if isinstance(val, str):
            parts.append(val)
        else:
            try:
                parts.append(json.dumps(val, default=str)[:limit])
            except (TypeError, ValueError):
                parts.append(str(val)[:limit])
    return "\n\n".join(parts)[:limit]


def _summarize(result: Any, *, limit: int = 220) -> str:
    if isinstance(result, dict):
        if result.get("results") is not None:
            return f"{len(result['results'])} result(s)"
        if result.get("title"):
            return f"{result['title']}"[:limit]
        if result.get("error"):
            return f"error: {result['error']}"[:limit]
    return str(result)[:limit]


def _coerce_depth(value: Any) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def _gate_spawn_depth(ann: Mapping[str, Any], context: Any) -> int:
    """Enforce the nesting cap before a subagent context is created.

    A top-level workflow subagent is depth 1 and always runs — the orchestrator
    spawning it is not itself an agent. Only a spawn from *inside* another agent
    is gated, which is why sibling fan-out in a phase is untouched: siblings read
    the same parent depth, they do not inherit each other's.
    """
    from CortexOS.execution.subagent_contract import assert_spawn_allowed

    parent_depth = _coerce_depth(
        ann.get("parent_spawn_depth")
        if ann.get("parent_spawn_depth") is not None
        else context.get("_spawn_depth")
    )
    if parent_depth >= 1:
        return assert_spawn_allowed(parent_depth)
    return _coerce_depth(ann.get("spawn_depth")) or 1


async def run_agent_task(
    node: DSLNode,
    context: Any,
    router: ModelRouter,
    ledger: CostLedger,
    *,
    workflow_cost_ceiling_myr: float,
) -> tuple[dict[str, Any], AgentTaskTelemetry]:
    """Execute one subagent. Returns (output, telemetry)."""
    ann = node.annotations if isinstance(node.annotations, dict) else {}
    spawn_depth = _gate_spawn_depth(ann, context)
    tools: list[str] = [str(t) for t in (ann.get("tools") or [])]
    label = str(ann.get("label") or node.id)
    telemetry = AgentTaskTelemetry(label=label, purpose=str(ann.get("purpose") or ""))
    started = time.monotonic()

    effort = str(ann.get("effort") or "medium").lower()
    default_tier, max_tier = _EFFORT_TIERS.get(effort, _EFFORT_TIERS["medium"])
    max_steps = int(ann.get("max_steps") or DEFAULT_MAX_STEPS)
    broker: ToolBroker = context.get("_tool_broker") or default_broker
    on_event = context.get("_on_event")

    system = "\n\n".join(x for x in (node.system or "", _tool_instructions(tools)) if x)
    upstream = _upstream_blob(node, context)
    base_prompt = node.prompt or ""
    if upstream:
        base_prompt = f"{base_prompt}\n\n--- input from the previous phase ---\n{upstream}"

    transcript: list[str] = []
    content = ""
    stop_reason = "max_steps"
    quality_passed: bool | None = None
    ended_on_tool = False
    tool_fingerprints: list[str] = []
    wf_ceiling = (
        workflow_cost_ceiling_myr if math.isfinite(workflow_cost_ceiling_myr) else 1e9
    )
    try:
        from CortexOS.execution.workflow_openvault import workflow_identity

        ov_identity = workflow_identity(str(context.run_id), node.id)
    except Exception:
        ov_identity = f"wf:{context.run_id}:{node.id}"

    for step in range(max_steps):
        telemetry.steps = step + 1
        telemetry.tool_calls.append(
            ToolCallRecord(tool="think", ok=True, ms=0, summary=f"step {telemetry.steps}")
        )
        prompt = base_prompt if not transcript else base_prompt + "\n\n" + "\n\n".join(transcript)
        model_req = ModelRequest(
            request_type=str(ann.get("prompt_id") or node.id),
            prompt=prompt,
            default_tier=Tier(default_tier),
            max_tier=Tier(max_tier),
            cost_ceiling_myr=node.cost_ceiling_myr or wf_ceiling,
            provider=node.provider,
            metadata={
                "workflow": ann.get("workflow"),
                "phase": ann.get("phase"),
                "openvault_identity": ov_identity,
                "effort": effort,
            },
        )
        adapter_req = AdapterRequest(
            model="",
            system=system,
            prompt=prompt,
            max_tokens=node.max_tokens if node.max_tokens is not None else 1400,
            tools=openai_tools(tools) if tools else None,
        )
        try:
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
        except Exception as exc:  # cost ceiling, adapter failure, provider down
            telemetry.error = str(exc)[:300]
            stop_reason = "error"
            break

        resp = outcome.response
        telemetry.prompt_tokens += int(resp.prompt_tokens or 0)
        telemetry.completion_tokens += int(resp.completion_tokens or 0)
        telemetry.cost_myr += float(outcome.cost_myr)
        telemetry.model = outcome.model
        telemetry.tier = outcome.tier
        content = resp.content or ""

        if callable(on_event):
            on_event(
                {
                    "type": "agent_step",
                    "node": node.id,
                    "label": label,
                    "step": telemetry.steps,
                    "tokens": telemetry.tokens,
                }
            )

        call = None
        if tools:
            call = native_tool_call(resp.raw, tools) or _parse_tool_call(content, tools)
        if call is None:
            ended_on_tool = False
            criteria = _quality_criteria(ann)
            if not criteria:
                stop_reason = "final"
                break
            verdict = await _independent_quality_check(
                content, criteria, context, step=telemetry.steps
            )
            if verdict.passed and not verdict.early_victory_risk:
                stop_reason = "quality"
                quality_passed = True
                break
            if telemetry.steps >= max_steps:
                stop_reason = "max_steps"
                quality_passed = False
                break
            transcript.append(
                "Verifier rejected the answer (independent of the generator).\n"
                f"Feedback:\n{verdict.feedback}\n"
                "Revise: call more tools or try another approach, then answer again."
            )
            continue

        fp = json.dumps({"tool": call["tool"], "params": call["params"]}, sort_keys=True)
        if tool_fingerprints and tool_fingerprints[-1] == fp:
            transcript.append(
                "Stuck: that exact tool call already ran. Do not repeat it. "
                "Answer from the results so far or try a different query or tool."
            )
            ended_on_tool = True
            continue

        tool_started = time.monotonic()
        try:
            result = broker(call["tool"], call["params"])
            ok = not (isinstance(result, dict) and result.get("ok") is False)
        except Exception as exc:
            result = {"ok": False, "error": str(exc)[:300]}
            ok = False
        tool_ms = int((time.monotonic() - tool_started) * 1000)
        telemetry.tool_calls.append(
            ToolCallRecord(tool=call["tool"], ok=ok, ms=tool_ms, summary=_summarize(result))
        )
        if callable(on_event):
            on_event(
                {
                    "type": "agent_tool",
                    "node": node.id,
                    "label": label,
                    "tool": call["tool"],
                    "ok": ok,
                    "ms": tool_ms,
                }
            )
        try:
            observation = json.dumps(result, default=str)[:6000]
        except (TypeError, ValueError):
            observation = str(result)[:6000]
        transcript.append(f"You called {call['tool']}({json.dumps(call['params'])[:400]}).\nResult:\n{observation}")
        tool_fingerprints.append(fp)
        ended_on_tool = True

    if (
        ended_on_tool
        and stop_reason == "max_steps"
        and not telemetry.error
        and transcript
    ):
        # Native tool_calls leave content empty. One un-tooled answer after the last observation.
        prompt = (
            base_prompt
            + "\n\n"
            + "\n\n".join(transcript)
            + "\n\nStop calling tools. Answer the user in plain text using the tool results."
        )
        adapter_req = AdapterRequest(
            model="",
            system=(node.system or "") + "\nAnswer in plain text. Do not call tools.",
            prompt=prompt,
            max_tokens=node.max_tokens if node.max_tokens is not None else 1400,
            tools=None,
        )
        try:
            outcome = await invoke_routed_completion(
                router,
                ledger,
                run_id=context.run_id,
                workflow_cost_ceiling_myr=workflow_cost_ceiling_myr,
                node_id=node.id,
                model_req=ModelRequest(
                    request_type=str(ann.get("prompt_id") or node.id),
                    prompt=prompt,
                    default_tier=Tier(default_tier),
                    max_tier=Tier(max_tier),
                    cost_ceiling_myr=node.cost_ceiling_myr or wf_ceiling,
                    provider=node.provider,
                    metadata={
                        "workflow": ann.get("workflow"),
                        "phase": ann.get("phase"),
                        "openvault_identity": ov_identity,
                        "effort": effort,
                    },
                ),
                adapter_req=adapter_req,
                node_cost_ceiling_myr=node.cost_ceiling_myr,
            )
            resp = outcome.response
            telemetry.prompt_tokens += int(resp.prompt_tokens or 0)
            telemetry.completion_tokens += int(resp.completion_tokens or 0)
            telemetry.cost_myr += float(outcome.cost_myr)
            telemetry.model = outcome.model
            telemetry.tier = outcome.tier
            content = resp.content or content
            criteria = _quality_criteria(ann)
            if not criteria:
                stop_reason = "final"
            else:
                verdict = await _independent_quality_check(
                    content, criteria, context, step=telemetry.steps
                )
                if verdict.passed and not verdict.early_victory_risk:
                    stop_reason = "quality"
                    quality_passed = True
                else:
                    quality_passed = False
        except Exception as exc:  # noqa: BLE001
            telemetry.error = str(exc)[:300]
            stop_reason = "error"

    telemetry.elapsed_ms = int((time.monotonic() - started) * 1000)
    telemetry.stop_reason = stop_reason
    telemetry.quality_passed = quality_passed
    # Local adapters sometimes omit usage — fall back to ~4 chars/token so the
    # panel still shows spend, flagged estimated by the runner/store.
    if not telemetry.prompt_tokens and not telemetry.completion_tokens and content:
        telemetry.completion_tokens = max(1, len(content) // 4)
        telemetry.prompt_tokens = max(1, len(base_prompt) // 4)

    # Subagent contract: final-message-only + instruction-injection sanitize
    # (distill: skill_distill/captures/2026-07-25_claude-code_all-lanes.md).
    from CortexOS.execution.subagent_contract import finalize_subagent_output

    raw_out: dict[str, Any] = {
        "content": content,
        "label": label,
        "purpose": telemetry.purpose,
        "phase": ann.get("phase"),
        "agent": ann.get("agent"),
        "telemetry": telemetry.as_dict(),
        "spawn_depth": spawn_depth,
        "stop_reason": stop_reason,
        "quality_passed": quality_passed,
    }
    parsed = _try_json(content)
    if parsed is not None:
        raw_out["data"] = parsed
    return finalize_subagent_output(raw_out), telemetry


def _try_json(text: str) -> Any | None:
    """Most workflow prompts pin a JSON return shape; keep it structured when
    the model complied, so the next phase can fan out over real items."""
    stripped = (text or "").strip()
    if stripped.startswith("```"):
        stripped = stripped.split("\n", 1)[-1]
        if stripped.endswith("```"):
            stripped = stripped[: stripped.rfind("```")]
        stripped = stripped.strip()
    if not stripped.startswith(("{", "[")):
        return None
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        return None
