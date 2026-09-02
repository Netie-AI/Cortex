"""A2 run-plan dispatch tests."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from CortexOS.execution.architecture_presets import resolve_runner
from CortexOS.execution.preset_router import plan_for_request
from CortexOS.execution.run_plan import execute_run_plan


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("preset", "runner"),
    [
        ("minimal", "dag_single"),
        ("sequential", "dag_linear"),
        ("dag", "dag_runner"),
    ],
)
async def test_dag_presets_execute(preset, runner):
    result = await execute_run_plan(plan_for_request(preset, {"prompt": "hello"}), {"prompt": "hello"})

    assert result["ok"] is True
    assert result["runner"] == runner
    assert result["status"] == "completed"


@pytest.mark.asyncio
async def test_dag_runner_accepts_inline_dag():
    result = await execute_run_plan(
        plan_for_request("dag", {"prompt": "hello"}),
        {
            "prompt": "hello",
            "dag": {
                "entry_node_id": "prompt",
                "output_node_id": "emit",
                "nodes": [
                    {"id": "prompt", "kind": "document_ref", "context_key": "prompt"},
                    {"id": "emit", "kind": "EMIT", "inputs": ["prompt"]},
                ],
            },
        },
    )

    assert result["ok"] is True
    assert result["output"] == {"prompt": "hello"}


@pytest.mark.asyncio
async def test_langgraph_reports_adapter_unavailable():
    result = await execute_run_plan(resolve_runner("langgraph"), {"prompt": "hello"})

    assert result == {
        "ok": False,
        "runner": "marketplace_langgraph",
        "status_code": 501,
        "error": "adapter_unavailable",
    }


@pytest.fixture
def engine_client(monkeypatch, tmp_path):
    monkeypatch.setenv("PACK", "dms")
    monkeypatch.setenv("DMS_AUTH_DISABLED", "1")
    from CortexOS.api.app import create_app

    return TestClient(create_app())


def test_engine_run_roundtrip(engine_client):
    response = engine_client.post(
        "/api/engine/run",
        json={"prompt": "dispatch this", "architecture_preset": "minimal", "session_id": "a2-test"},
    )

    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert response.json()["runner"] == "dag_single"
    assert response.json()["run_id"] == "a2-test"


def test_agent_preset_resolves_to_agent_task():
    plan = resolve_runner("agent")
    assert plan["runner"] == "agent_task"
    assert plan["preset"] == "agent"


def test_engine_agent_loop_http(engine_client, monkeypatch):
    from CortexOS.execution.executor import RoutedCompletionOutcome
    from CortexOS.routing.adapters.base import AdapterResponse

    def _out(text: str) -> RoutedCompletionOutcome:
        return RoutedCompletionOutcome(
            response=AdapterResponse(
                content=text, prompt_tokens=8, completion_tokens=4, latency_ms=1, raw={}
            ),
            tier="T1",
            model="stub",
            cost_myr=0.0,
        )

    async def fake(*_a, **kw):
        req = kw.get("model_req")
        rtype = getattr(req, "request_type", "") or ""
        if rtype == "agent_task.verify":
            return _out(
                '{"passed": true, "feedback": "ok", '
                '"criteria_checked": ["addresses the user prompt", "does not invent tool results"]}'
            )
        return _out("the sky is blue")

    monkeypatch.setattr("CortexOS.execution.agent_task.invoke_routed_completion", fake)
    monkeypatch.setattr("CortexOS.execution.executor.invoke_routed_completion", fake)

    response = engine_client.post(
        "/api/engine/run",
        json={
            "prompt": "what color is the sky",
            "architecture_preset": "agent",
            "session_id": "loop-02",
            "max_steps": 3,
        },
    )
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["ok"] is True
    assert data["runner"] == "agent_task"
    assert data["stop_reason"] == "quality"
    assert data["quality_passed"] is True
    assert "sky" in (data.get("content") or "")

