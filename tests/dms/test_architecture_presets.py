"""Architecture preset catalog + OpenVault gate client smoke."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from CortexOS.execution.architecture_presets import catalog, normalize_preset, resolve_runner
from CortexOS.execution.preset_router import plan_for_request


@pytest.fixture
def engine_client(monkeypatch, tmp_path):
    monkeypatch.setenv("PACK", "dms")
    monkeypatch.setenv("DMS_AUTH_DISABLED", "1")
    from CortexOS.api.app import create_app

    return TestClient(create_app())


def test_normalize_preset_defaults():
    assert normalize_preset(None) == "minimal"
    assert normalize_preset("DAG") == "dag"
    assert normalize_preset("nope") == "minimal"


def test_catalog_has_moe_presets():
    ids = {p["id"] for p in catalog()}
    assert {"dag", "sequential", "langgraph", "minimal", "rag", "memory", "computer_control"} <= ids


def test_resolve_runner_requires_openvault_gate():
    plan = resolve_runner("dag")
    assert plan["runner"] == "dag_runner"
    assert plan["openvault_gate_required"] is True


def test_plan_for_request_sets_gate_action():
    p = plan_for_request("rag", {"prompt": "hi", "intent": "deploy_to_web"})
    assert p["gate_action"] == "deploy"
    assert p["preset"] == "rag"


def test_engine_backends_lists_architecture_presets(engine_client):
    res = engine_client.get("/api/engine/backends")
    assert res.status_code == 200
    data = res.json()
    assert any(p["id"] == "minimal" for p in data["architecture_presets"])


def test_engine_config_architecture_preset_roundtrip(engine_client):
    res = engine_client.post(
        "/api/engine/config",
        json={"architecture_preset": "dag", "agents_runtime": "cortex"},
    )
    assert res.status_code == 200
    data = res.json()
    assert data["ok"] is True
    assert data["config"]["architecture_preset"] == "dag"
    assert data["run_plan"]["runner"] == "dag_runner"
