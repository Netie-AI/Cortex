"""Constructor graph compile + key-gated /cortex mount."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from CortexOS.constructor_graph import ConstructorGraphError, compile_constructor_graph
from packs.dms.security.rate_limit import reset_limiter

SAMPLE = {
    "nodes": [
        {"id": "n1", "kind": "ingest", "x": 48, "y": 72, "note": "Read operations into the graph."},
        {"id": "n2", "kind": "hypothesize", "x": 280, "y": 72, "note": "Surface a testable claim."},
        {"id": "n3", "kind": "improve", "x": 512, "y": 72, "note": "Change a product from the claim."},
        {"id": "n4", "kind": "audit", "x": 280, "y": 220, "note": "Show why this node exists."},
    ],
    "edges": [
        {"from": "n1", "to": "n2"},
        {"from": "n2", "to": "n3"},
        {"from": "n3", "to": "n4"},
    ],
}


@pytest.fixture
def api_keys_env(monkeypatch):
    monkeypatch.setenv(
        "DMS_API_KEYS",
        "viewer:sk-viewer-test;steward:sk-steward-test;admin:sk-admin-test",
    )
    monkeypatch.delenv("DMS_AUTH_DISABLED", raising=False)
    monkeypatch.setenv("PACK", "dms")
    monkeypatch.setenv("CONSTRUCTOR_SKIN_DIR", r"D:\Constructor")
    return {"viewer": "sk-viewer-test"}


@pytest.fixture
def dms_client(api_keys_env, tmp_path, monkeypatch):
    reset_limiter(per_minute=120)
    monkeypatch.setenv("DMS_OPS_DB", str(tmp_path / "ops.db"))
    from CortexOS.api.app import create_app

    return TestClient(create_app())


def test_compile_sample_has_four_emit_nodes():
    program = compile_constructor_graph(SAMPLE)
    assert [n.id for n in program.nodes] == ["n1", "n2", "n3", "n4"]
    assert program.nodes[1].inputs == ["n1"]
    assert program.entry_node_id == "n1"
    assert program.output_node_id == "n4"


def test_compile_rejects_unknown_kind():
    with pytest.raises(ConstructorGraphError):
        compile_constructor_graph({"nodes": [{"id": "x", "kind": "n8n"}], "edges": []})


def test_login_open_constructor_redirects_without_key(dms_client):
    login = dms_client.get("/cortex/login")
    assert login.status_code == 200
    assert "API key" in login.text
    bare = dms_client.get("/cortex/constructor/", follow_redirects=False)
    assert bare.status_code == 303
    assert "/cortex/login" in bare.headers["location"]


def test_constructor_html_requires_key(dms_client, api_keys_env):
    denied = dms_client.get("/cortex/constructor/", follow_redirects=False)
    assert denied.status_code == 303
    ok = dms_client.get(
        "/cortex/constructor/",
        headers={"X-API-Key": api_keys_env["viewer"]},
        follow_redirects=False,
    )
    assert ok.status_code == 200
    assert b"id=\"stage\"" in ok.content


def test_run_401_without_key(dms_client):
    res = dms_client.post("/cortex/constructor/run", json=SAMPLE)
    assert res.status_code == 401


def test_run_compiles_with_viewer_key(dms_client, api_keys_env):
    res = dms_client.post(
        "/cortex/constructor/run",
        json=SAMPLE,
        headers={"X-API-Key": api_keys_env["viewer"]},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert set(body["nodes"]) == {"n1", "n2", "n3", "n4"}
    assert body["actor"] == "api_viewer"
