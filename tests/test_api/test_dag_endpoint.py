import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient
from netie.api.app import create_app
from netie.execution.model_router import BIG_API_PLACEHOLDER, ModelRouter

from tests.test_execution.test_cost_ledger_and_executor import StubAdapter


def test_judged_node_end_to_end_via_test_client():
    app = create_app()

    dag = {
        "version": "2.0",
        "intent_hash": "h",
        "entry_node_id": "j1",
        "output_node_id": "e1",
        "nodes": [
            {
                "id": "j1",
                "kind": "llm_judged",
                "default_tier": "T3",
                "max_tier": "T3",
                "provider": BIG_API_PLACEHOLDER,
                "prompt": "spa stamp duty wording",
                "max_tokens": 40,
                "cost_ceiling_myr": 10.0,
                "inputs": [],
            },
            {"id": "e1", "kind": "EMIT", "tier": 0, "inputs": ["j1"]},
        ],
    }

    with TestClient(app) as client:
        stub = StubAdapter()
        app.state.model_router = ModelRouter(
            adapter_registry={"anthropic": stub, "openai": stub, "self_hosted": stub},
            provider_aliases={BIG_API_PLACEHOLDER: "anthropic"},
        )
        r = client.post(
            "/run",
            json={
                "dag": dag,
                "run_id": "api_run",
                "context": {},
            },
        )
    assert r.status_code == 200
    payload = r.json()
    assert payload["run_id"] == "api_run"
    assert payload["nodes"]["j1"]["output"]["content"] == "ok"
    assert payload["total_myr"] > 0
    recs = app.state.ledger.records()
    assert len(recs) == 1
    assert recs[0].status == "ok"
    assert recs[0].cost_myr > 0


def test_run_cost_endpoint_returns_total_and_records():
    app = create_app()

    with TestClient(app) as client:
        stub = StubAdapter()
        app.state.model_router = ModelRouter(
            adapter_registry={"anthropic": stub, "openai": stub, "self_hosted": stub},
            provider_aliases={BIG_API_PLACEHOLDER: "anthropic"},
        )
        dag = {
            "version": "2.0",
            "intent_hash": "h",
            "entry_node_id": "j1",
            "output_node_id": "e1",
            "nodes": [
                {
                    "id": "j1",
                    "kind": "llm_judged",
                    "default_tier": "T3",
                    "max_tier": "T3",
                    "provider": BIG_API_PLACEHOLDER,
                    "prompt": "cost probe",
                    "max_tokens": 40,
                    "cost_ceiling_myr": 10.0,
                    "inputs": [],
                },
                {"id": "e1", "kind": "EMIT", "tier": 0, "inputs": ["j1"]},
            ],
        }
        run = client.post("/run", json={"dag": dag, "run_id": "cost_probe"})
        assert run.status_code == 200
        cost = client.get("/api/engine/runs/cost_probe/cost")
    assert cost.status_code == 200
    body = cost.json()
    assert body["run_id"] == "cost_probe"
    assert body["total_myr"] > 0
    assert len(body["records"]) == 1
    assert body["records"][0]["node_id"] == "j1"
