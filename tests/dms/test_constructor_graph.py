"""Constructor compile / fetch / run / OpenVault session."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from packs.dms.constructor_app import create_constructor_app
from packs.dms.constructor_graph import compile_ir, recommend

FOUNDRY = {
    "nodes": [
        {
            "id": "c1",
            "kind": "connector",
            "object_type": "inventory",
            "data_point": "sku",
            "fetch_from": "warehouse.inventory",
        },
        {"id": "o1", "kind": "ontology", "object_type": "suppliers", "fetch_from": "warehouse.suppliers"},
        {"id": "i1", "kind": "insight"},
        {"id": "f1", "kind": "foundry", "action_type": "export_pptx"},
        {"id": "a1", "kind": "app", "action_type": "emit"},
        {"id": "t1", "kind": "tool_call", "action_type": "export_pptx"},
    ],
    "edges": [
        {"from": "c1", "to": "o1"},
        {"from": "o1", "to": "i1"},
        {"from": "i1", "to": "f1"},
        {"from": "f1", "to": "a1"},
        {"from": "f1", "to": "t1"},
    ],
}


@pytest.fixture
def api_keys_env(monkeypatch, tmp_path):
    monkeypatch.setenv(
        "DMS_API_KEYS",
        "viewer:sk-viewer-test;steward:sk-steward-test;admin:sk-admin-test",
    )
    monkeypatch.delenv("DMS_AUTH_DISABLED", raising=False)
    monkeypatch.delenv("DMS_REFUSE_DEMO_KEYS", raising=False)
    monkeypatch.setenv("PACK", "dms")
    monkeypatch.setenv("DMS_WAREHOUSE_DB", str(tmp_path / "wh.duckdb"))
    monkeypatch.setenv("CONSTRUCTOR_SKIN_DIR", str(Path(r"E:\Constructor")))
    return {"steward": "sk-steward-test", "viewer": "sk-viewer-test"}


@pytest.fixture
def client(api_keys_env):
    return TestClient(create_constructor_app())


def test_compile_foundry_emits_app():
    ir = compile_ir(FOUNDRY, ghost=True)
    assert ir["ok"] is True
    assert ir["output_node_id"] == "a1"
    kinds = {n["id"]: n["kind"] for n in ir["nodes"]}
    assert kinds["a1"] == "EMIT"
    assert kinds["t1"] == "TOOL_CALL"


def test_recommend_foundry_picks_orchestrator():
    rec = recommend(FOUNDRY)
    assert rec["ok"] is True
    assert rec["recommendation"]["pattern"] == "orchestrator_subagent"


def test_login_and_skin(client):
    login = client.get("/cortex/login")
    assert login.status_code == 200
    assert "ov_" in login.text
    home = client.get("/cortex", follow_redirects=False)
    assert home.status_code == 307
    skin = client.get("/cortex/constructor/")
    assert skin.status_code == 200
    assert "Constructor" in skin.text


def test_ontology_catalog(client):
    res = client.get("/cortex/constructor/ontology")
    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is True
    assert "inventory" in body["objects"]
    assert "sku" in body["objects"]["inventory"]["points"]
    assert "export_pptx" in body["actions"]
    assert "warehouse.inventory" in body["fetch_places"]


def test_ghost_no_key(client):
    res = client.post("/cortex/constructor/ghost", json=FOUNDRY)
    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is True
    assert body["ghost"] is True
    assert all(step["write"] is False for step in body["walk"])


def test_fetch_requires_key(client):
    res = client.post(
        "/cortex/constructor/fetch",
        json={"nodes": [FOUNDRY["nodes"][0]], "edges": []},
    )
    assert res.status_code == 401


def test_fetch_and_run(client, api_keys_env, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    headers = {"X-API-Key": api_keys_env["steward"]}
    fetch = client.post(
        "/cortex/constructor/fetch",
        json={"nodes": [FOUNDRY["nodes"][0]], "edges": []},
        headers=headers,
    )
    assert fetch.status_code == 200, fetch.text
    slice_ = fetch.json()["slice"]
    assert slice_["table"] == "inventory"
    assert slice_["row_count"] > 0
    assert not slice_.get("error")

    run = client.post("/cortex/constructor/run", json=FOUNDRY, headers=headers)
    assert run.status_code == 200, run.text
    body = run.json()
    assert body["ok"] is True
    assert body["actor"] == "api_steward"
    assert "c1" in body["fetches"]
    assert body["wrote"] and body["wrote"].endswith("export.pptx")
    assert Path(body["wrote"]).is_file()


def test_session_cookie(client, api_keys_env):
    res = client.post("/cortex/session", json={"key": api_keys_env["steward"]})
    assert res.status_code == 200
    assert res.json()["ok"] is True
    assert client.cookies.get("cortex_session") == api_keys_env["steward"]
    fetch = client.post(
        "/cortex/constructor/fetch",
        json={"nodes": [FOUNDRY["nodes"][0]], "edges": []},
    )
    assert fetch.status_code == 200


def test_issue_key_uses_openvault(client, monkeypatch):
    monkeypatch.setattr(
        "CortexOS.integrations.openvault_keys.issue_token",
        lambda label="constructor", tier="free": {
            "ok": True,
            "token": "ov_testtoken1234567890",
            "key": {"key_id": "abc123"},
        },
    )
    res = client.post("/cortex/constructor/issue-key", json={})
    assert res.status_code == 200
    assert res.json()["token"].startswith("ov_")


def test_ov_key_verify(client, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "CortexOS.integrations.openvault_keys.verify_token",
        lambda token: {"ok": True, "key": {"key_id": "ov1"}} if token.startswith("ov_") else None,
    )
    res = client.post(
        "/cortex/constructor/fetch",
        json={"nodes": [FOUNDRY["nodes"][0]], "edges": []},
        headers={"X-API-Key": "ov_live_example_token"},
    )
    assert res.status_code == 200, res.text
    assert res.json()["actor"] == "ov_ov1"
