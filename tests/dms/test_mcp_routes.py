"""A6 — first-party read-only MCP HTTP surface."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("PACK", "dms")
    monkeypatch.setenv("DMS_AUTH_DISABLED", "1")
    from CortexOS.api.app import create_app

    return TestClient(create_app())


def test_mcp_list_tools(client):
    res = client.get("/mcp/tools")
    assert res.status_code == 200
    data = res.json()
    assert data["ok"] is True
    names = {t["name"] for t in data["tools"]}
    assert "answer_engine.answer" in names
    assert "lakehouse.tables" in names
    assert "agent.status" in names
    assert "auto_caller.pick" in names
    assert "constructor.ghost" in names


def test_mcp_call_agent_status(client):
    res = client.post("/mcp/call", json={"name": "agent.status", "arguments": {}})
    assert res.status_code == 200
    data = res.json()
    assert data["ok"] is True
    assert data["result"]["engine"] == "cortex"


def test_mcp_unknown_tool(client):
    res = client.post("/mcp/call", json={"name": "nope.tool", "arguments": {}})
    assert res.status_code == 404
