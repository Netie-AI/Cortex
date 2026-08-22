"""Workspace dispatch + Cursor session port.

distill: skill_distill/captures/2026-08-22_cursor_orchestration-outside-editor.md
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from CortexOS.connectors import cursor_session, workspaces
from CortexOS.connectors.dispatch import dispatch

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def _isolate(tmp_path):
    cursor_session.reset_for_tests(tmp_path / "chats.json")
    yield
    cursor_session.reset_for_tests()


def test_new_task_opens_a_new_cursor_chat_each_time():
    a = dispatch("fix the warehouse low-stock query", kind="task")
    b = dispatch("fix the warehouse low-stock query", kind="task")
    assert a["new_cursor_chat"] is True
    assert b["new_cursor_chat"] is True
    assert a["cursor_chat_id"] != b["cursor_chat_id"]
    assert a["workspace"] == "dms"
    assert a["surface"] == "cursor_new_chat"
    assert a["orchestrator"] == "cortex"


def test_normal_chat_stays_on_chatbot_and_does_not_open_cursor():
    out = dispatch("just chatting about the weather", kind="chat")
    assert out["kind"] == "chat"
    assert out["workspace"] == "chatbot"
    assert out["new_cursor_chat"] is False
    assert out["cursor_chat_id"] is None
    assert out["surface"] == "chatbot"
    assert cursor_session.get_port().list_chats() == []


def test_netie_language_routes_to_netie_workspace():
    out = dispatch("ingest a distill capture into netie kb", kind="task")
    assert out["workspace"] == "netie"
    assert out["new_cursor_chat"] is True


def test_retrieve_and_instruct_cursor_messages():
    out = dispatch("build the connector port in cortex", kind="task")
    chat_id = out["cursor_chat_id"]
    assert out["workspace"] == "cortex"
    port = cursor_session.get_port()
    first = port.messages(chat_id)
    assert first[0]["text"].startswith("build the connector")
    port.instruct(chat_id, "also list workspaces")
    texts = [m["text"] for m in port.messages(chat_id)]
    assert "also list workspaces" in texts


def test_env_overrides_workspace_root(monkeypatch, tmp_path):
    monkeypatch.setenv("CORTEX_WS_DMS", str(tmp_path / "Dms"))
    (tmp_path / "Dms").mkdir()
    row = workspaces.get("dms")
    assert row["root"] == str(tmp_path / "Dms")
    assert row["present"] is True


def test_unknown_workspace_raises():
    with pytest.raises(KeyError):
        workspaces.get("salesforce")


def test_connectors_package_does_not_import_langgraph_or_packs():
    for py in (ROOT / "CortexOS" / "connectors").glob("*.py"):
        tree = ast.parse(py.read_text(encoding="utf-8"), filename=str(py))
        mods: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                mods.extend(a.name for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                mods.append(node.module)
        for mod in mods:
            assert not mod.startswith("langgraph"), py.name
            assert not mod.startswith("crewai"), py.name
            assert not mod.startswith("packs"), py.name
