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
