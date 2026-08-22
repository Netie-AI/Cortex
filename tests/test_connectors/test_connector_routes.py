"""HTTP surface for /api/connectors."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from CortexOS.connectors import agents, cursor_session


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("PACK", "dms")
    monkeypatch.setenv("DMS_AUTH_DISABLED", "1")
    monkeypatch.delenv("CORTEX_COMPUTER_CONTROL", raising=False)
    monkeypatch.delenv("CORTEX_COMPUTER_CONTROL_EXECUTE", raising=False)
    cursor_session.reset_for_tests(tmp_path / "chats.json")
    agents.reset_for_tests()
    from CortexOS.api.app import create_app

    with TestClient(create_app()) as c:
        yield c
    cursor_session.reset_for_tests()
    agents.reset_for_tests()


def test_workspaces_and_ui(client):
    res = client.get("/api/connectors/workspaces")
    assert res.status_code == 200
    ids = {w["id"] for w in res.json()["workspaces"]}
    assert {"cortex", "netie", "dms", "chatbot", "pointer", "omi", "openvault"} <= ids
    ui = client.get("/api/connectors")
    assert ui.status_code == 200
    assert "text/html" in ui.headers.get("content-type", "")
    assert "Not LangGraph" in ui.text
    assert "Constructor Agent" in ui.text
    assert "Message Constructor Agent" in ui.text


def test_dispatch_task_then_instruct_and_retrieve(client):
    r = client.post(
        "/api/connectors/dispatch",
        json={"text": "ship the next DMS warehouse slice", "kind": "task"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["workspace"] == "dms"
    assert body["new_cursor_chat"] is True
    chat_id = body["cursor_chat_id"]
    assert chat_id

    inst = client.post(
        f"/api/connectors/cursor/chats/{chat_id}/instruct",
        json={"instruction": "run pytest on the slice"},
    )
    assert inst.status_code == 200
    msgs = client.get(f"/api/connectors/cursor/chats/{chat_id}/messages")
    assert msgs.status_code == 200
    texts = [m["text"] for m in msgs.json()["messages"]]
    assert "run pytest on the slice" in texts


def test_chat_kind_does_not_open_cursor(client):
    r = client.post(
        "/api/connectors/dispatch",
        json={"text": "hello there", "kind": "chat"},
    )
    assert r.status_code == 200
    assert r.json()["cursor_chat_id"] is None
    listed = client.get("/api/connectors/cursor/chats")
    assert listed.json()["chats"] == []


def test_agent_message_roundtrip(client):
    listed = client.get("/api/connectors/agents")
    assert listed.status_code == 200
    names = {a["id"] for a in listed.json()["agents"]}
    assert "constructor" in names
    assert "pointer" in names
    posted = client.post(
        "/api/connectors/agents/constructor/messages",
        json={"text": "build the connector desk", "kind": "task"},
    )
    assert posted.status_code == 200
    body = posted.json()
    assert body["dispatch"]["workspace"] == "cortex"
    assert body["dispatch"]["new_cursor_chat"] is True
    hist = client.get("/api/connectors/agents/constructor/messages")
    texts = [m["text"] for m in hist.json()["messages"]]
    assert "build the connector desk" in texts
    assert any("Constructor Agent took it" in t for t in texts)


def test_computer_control_probe_default_off_and_invoke_fails(client):
    status = client.get("/api/connectors/computer-control")
    assert status.status_code == 200
    body = status.json()
    assert body["enabled"] is False
    assert body["armed"] is False
    assert body["can_control"] is False
    click = client.post(
        "/api/connectors/computer-control/invoke",
        json={"action": "click", "x": 1, "y": 1},
    )
    assert click.status_code == 403
    detail = click.json()["detail"]
    assert detail["ok"] is False
    assert detail["executed"] is False
