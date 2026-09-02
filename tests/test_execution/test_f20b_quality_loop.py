"""F20b: AGENT_TASK stops on independent quality, not only max_steps.

distill: skill_distill/captures/2026-08-28_oss_openmanus-openworker-rakazo.md
"""

from __future__ import annotations

import asyncio

from CortexOS.execution.agent_task import run_agent_task
from CortexOS.execution.dag_runner import ExecutionContext
from CortexOS.execution.executor import RoutedCompletionOutcome
from CortexOS.fabrication.dsl_parser import DSLNode, NodeType
from CortexOS.routing.adapters.base import AdapterResponse


def _outcome(text: str) -> RoutedCompletionOutcome:
    return RoutedCompletionOutcome(
        response=AdapterResponse(
            content=text,
            prompt_tokens=8,
            completion_tokens=4,
            latency_ms=1,
            raw={},
        ),
        tier="T1",
        model="stub",
        cost_myr=0.0,
    )


def _script(monkeypatch, texts: list[str]) -> list[str]:
    seen: list[str] = []
    it = iter(texts)

    async def fake(*_a, **_k):
        text = next(it)
        seen.append(text)
        return _outcome(text)

    monkeypatch.setattr(
        "CortexOS.execution.agent_task.invoke_routed_completion", fake
    )
    return seen


def _node(**ann):
    return DSLNode(
        id="a1",
        type=NodeType.AGENT_TASK,
        inputs=[],
        prompt="answer the question",
        annotations={"label": "qa", "max_steps": 3, **ann},
    )


def _run(node, ctx):
    return asyncio.run(run_agent_task(node, ctx, None, None, workflow_cost_ceiling_myr=1.0))


def test_no_criteria_first_plain_text_still_stops(monkeypatch):
    _script(monkeypatch, ["the sky is blue", "should not run"])
    out, tel = _run(_node(), ExecutionContext("run-final", {}))
    assert tel.stop_reason == "final"
    assert tel.quality_passed is None
    assert tel.steps == 1
    assert out["stop_reason"] == "final"


def test_quality_fail_then_pass_continues(monkeypatch):
    _script(monkeypatch, ["draft v1", "draft v2 with evidence"])

    def verify(content, _criteria, _step):
        ok = "evidence" in content
        return {
            "passed": ok,
            "feedback": "need evidence" if not ok else "ok",
            "criteria_checked": ["has evidence"],
        }

    ctx = ExecutionContext("run-q", {"_quality_verify": verify})
    out, tel = _run(_node(quality_criteria=["has evidence"]), ctx)
    assert tel.stop_reason == "quality"
    assert tel.quality_passed is True
    assert tel.steps == 2
    assert "evidence" in out["content"]


def test_quality_never_passes_hits_ceiling(monkeypatch):
    _script(monkeypatch, ["bad", "still bad", "also bad"])

    def verify(_content, _criteria, _step):
        return {"passed": False, "feedback": "no", "criteria_checked": ["x"]}

    ctx = ExecutionContext("run-cap", {"_quality_verify": verify})
    out, tel = _run(_node(quality_criteria=["x"], max_steps=2), ctx)
    assert tel.stop_reason == "max_steps"
    assert tel.quality_passed is False
    assert tel.steps == 2
    assert out["quality_passed"] is False


def test_unbound_verifier_fail_closed(monkeypatch):
    _script(monkeypatch, ["looks done", "still looks done"])
    out, tel = _run(
        _node(quality_criteria=["must cite a source"], max_steps=2),
        ExecutionContext("run-none", {}),
    )
    assert tel.stop_reason == "max_steps"
    assert tel.quality_passed is False
    assert tel.steps == 2
    assert out["stop_reason"] == "max_steps"


def test_early_victory_is_not_a_pass(monkeypatch):
    _script(monkeypatch, ["lgtm", "real answer with both bars"])

    def verify(content, _criteria, _step):
        if "both bars" in content:
            return {
                "passed": True,
                "feedback": "ok",
                "criteria_checked": ["a", "b"],
            }
        return {"passed": True, "feedback": "lgtm"}

    ctx = ExecutionContext("run-ev", {"_quality_verify": verify})
    out, tel = _run(_node(quality_criteria=["a", "b"]), ctx)
    assert tel.stop_reason == "quality"
    assert tel.steps == 2
    assert "both bars" in out["content"]


def test_native_openai_tool_call_runs_broker(monkeypatch):
    from CortexOS.execution.agent_task import native_tool_call

    raw = {
        "choices": [
            {
                "message": {
                    "content": "",
                    "tool_calls": [
                        {
                            "id": "c1",
                            "function": {
                                "name": "web_search",
                                "arguments": '{"query": "OpenManus"}',
                            },
                        }
                    ],
                }
            }
        ]
    }
    assert native_tool_call(raw, ["web_search"]) == {
        "tool": "web_search",
        "params": {"query": "OpenManus"},
    }

    calls = []
    seen_tools: list = []

    def broker(name, params):
        calls.append((name, params))
        return {"ok": True, "results": [{"title": "OpenManus"}]}

    it = iter(["", "OpenManus is an MIT agent framework."])

    async def fake(*_a, **kw):
        text = next(it)
        req = kw.get("adapter_req")
        if req is not None:
            seen_tools.append(getattr(req, "tools", None))
        return RoutedCompletionOutcome(
            response=AdapterResponse(
                content=text,
                prompt_tokens=8,
                completion_tokens=4,
                latency_ms=1,
                raw=raw if text == "" else {},
            ),
            tier="T1",
            model="stub",
            cost_myr=0.0,
        )

    monkeypatch.setattr("CortexOS.execution.agent_task.invoke_routed_completion", fake)
    ctx = ExecutionContext(
        "run-nt",
        {
            "_tool_broker": broker,
            "_quality_verify": lambda *_a: {
                "passed": True,
                "feedback": "ok",
                "criteria_checked": ["x"],
            },
        },
    )
    out, tel = _run(_node(tools=["web_search"], quality_criteria=["x"]), ctx)
    assert calls == [("web_search", {"query": "OpenManus"})]
    assert tel.tool_calls[0].tool == "web_search"
    assert tel.stop_reason == "quality"
    assert "OpenManus" in out["content"]
    assert seen_tools and seen_tools[0]
    assert seen_tools[0][0]["function"]["name"] == "web_search"


def test_tool_then_final_answer_when_steps_exhausted(monkeypatch):
    calls = []

    def broker(name, params):
        calls.append((name, params))
        return {"ok": True, "results": [{"title": "OpenManus", "url": "https://github.com/FoundationAgents/OpenManus"}]}

    it = iter(
        [
            '{"tool":"web_search","params":{"query":"OpenManus"}}',
            "OpenManus is an MIT agent framework at github.com/FoundationAgents/OpenManus.",
        ]
    )

    async def fake(*_a, **_k):
        return _outcome(next(it))

    monkeypatch.setattr("CortexOS.execution.agent_task.invoke_routed_completion", fake)
    ctx = ExecutionContext(
        "run-final-after-tool",
        {
            "_tool_broker": broker,
            "_quality_verify": lambda *_a: {
                "passed": True,
                "feedback": "ok",
                "criteria_checked": ["x"],
            },
        },
    )
    out, tel = _run(_node(tools=["web_search"], quality_criteria=["x"], max_steps=1), ctx)
    assert calls == [("web_search", {"query": "OpenManus"})]
    assert tel.stop_reason == "quality"
    assert "FoundationAgents" in out["content"]


def test_duplicate_tool_call_is_not_rerun(monkeypatch):
    calls = []

    def broker(name, params):
        calls.append((name, params))
        return {"ok": True, "results": [{"title": "once"}]}

    it = iter(
        [
            '{"tool":"web_search","params":{"query":"x"}}',
            '{"tool":"web_search","params":{"query":"x"}}',
            "Answer from the first search.",
        ]
    )

    async def fake(*_a, **_k):
        return _outcome(next(it))

    monkeypatch.setattr("CortexOS.execution.agent_task.invoke_routed_completion", fake)
    ctx = ExecutionContext(
        "run-stuck",
        {
            "_tool_broker": broker,
            "_quality_verify": lambda *_a: {
                "passed": True,
                "feedback": "ok",
                "criteria_checked": ["x"],
            },
        },
    )
    out, tel = _run(_node(tools=["web_search"], quality_criteria=["x"], max_steps=3), ctx)
    assert calls == [("web_search", {"query": "x"})]
    assert tel.stop_reason == "quality"
    assert "first search" in out["content"]
