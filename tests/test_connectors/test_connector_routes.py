"""HTTP surface for /api/connectors."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from CortexOS.connectors import cursor_session


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("PACK", "dms")
    monkeypatch.setenv("DMS_AUTH_DISABLED", "1")
    cursor_session.reset_for_tests(tmp_path / "chats.json")
    from CortexOS.api.app import create_app

    with TestClient(create_app()) as c:
        yield c
    cursor_session.reset_for_tests()


def test_workspaces_and_ui(client):
    res = client.get("/api/connectors/workspaces")
    assert res.status_code == 200
    ids = {w["id"] for w in res.json()["workspaces"]}
    assert ids == {"cortex", "netie", "dms", "chatbot"}
    ui = client.get("/api/connectors")
    assert ui.status_code == 200
    assert "text/html" in ui.headers.get("content-type", "")
    assert "Not LangGraph" in ui.text


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
