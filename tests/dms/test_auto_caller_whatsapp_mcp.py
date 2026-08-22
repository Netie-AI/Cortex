"""WhatsApp is draft-only; auto-caller finds first-party tools and parks community MCP."""

from __future__ import annotations

import inspect

import pytest
from fastapi.testclient import TestClient

from CortexOS.constructor_graph import compile_constructor_graph
from CortexOS.discovery.auto_caller import pick
from CortexOS.discovery.find import find_mcp
from packs.dms.generative.brain import draft_whatsapp
from packs.dms.security.rate_limit import reset_limiter

SAMPLE = {
    "nodes": [
        {"id": "n1", "kind": "ingest"},
        {"id": "n2", "kind": "audit"},
    ],
    "edges": [{"from": "n1", "to": "n2"}],
}


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("PACK", "dms")
    monkeypatch.setenv("DMS_AUTH_DISABLED", "1")
    reset_limiter(per_minute=120)
    from CortexOS.api.app import create_app

    return TestClient(create_app())


def test_whatsapp_source_is_draft_not_a_sender():
    src = inspect.getsource(draft_whatsapp).lower()
    for banned in ("twilio", "pywhatkit", "baileys", "selenium", "whatsapp-web"):
        assert banned not in src
    assert "requires_confirm" in src


def test_auto_caller_parks_community_whatsapp_mcp():
    research = find_mcp("whatsapp send message", top_k=8)
    names = " ".join(
        (m.get("name") or "") + " " + (m.get("id") or "")
        for m in research.get("matches") or []
    ).lower()
    assert "whatsapp" in names

    decided = pick("send whatsapp to warehouse staff")
    assert decided["ok"] is True
    assert decided["live_whatsapp_connector"] is False
    assert decided["first_party"][0]["name"] == "brain.draft_whatsapp"
    assert decided["first_party"][0]["path"] == "/dms/brain/whatsapp"
    assert decided["community_mcp"]
    assert all(row["p16_parked"] for row in decided["community_mcp"])
    hint = " ".join(row.get("install_hint") or "" for row in decided["community_mcp"]).lower()
    assert "p16" in hint


def test_auto_caller_finds_constructor_memory_and_airgpt_dms():
    ctor = pick("compile constructor foundry canvas workflow")
    assert ctor["first_party"][0]["name"] == "constructor.ghost"
    assert "constructor_ghost" in {r["name"] for r in ctor["airgpt_tools"]}

    mem = pick("recall persisted knn memory")
    assert mem["first_party"][0]["name"] == "memory.query"
    assert "memory_search" in {r["name"] for r in mem["airgpt_tools"]}

    dms = pick("read warehouse inventory supplier objects")
    assert "dms_query" in {r["name"] for r in dms["airgpt_tools"]}


def test_mcp_auto_caller_and_constructor_ghost(client):
    listed = client.get("/mcp/tools").json()
    names = {t["name"] for t in listed["tools"]}
    assert {"auto_caller.pick", "constructor.ghost", "constructor.recommend", "memory.query"} <= names
    assert "whatsapp.send" not in names

    picked = client.post(
        "/mcp/call",
        json={"name": "auto_caller.pick", "arguments": {"goal": "whatsapp connector"}},
    )
    assert picked.status_code == 200, picked.text
    body = picked.json()["result"]
    assert body["live_whatsapp_connector"] is False

    ghost = client.post(
        "/mcp/call",
        json={"name": "constructor.ghost", "arguments": SAMPLE},
    )
    assert ghost.status_code == 200, ghost.text
    result = ghost.json()["result"]
    assert result["ghost"] is True
    assert result["output_node_id"] == "n2"
    compiled = compile_constructor_graph(SAMPLE)
    assert compiled.output_node_id == result["output_node_id"]
