"""Dispatch work to a workspace + Cursor/chatbot surface.

kind=task always opens a NEW Cursor chat. kind=chat never does — it stays
on the chatbot workspace. Cortex is the orchestrator; LangGraph is not.

distill: skill_distill/captures/2026-08-22_cursor_orchestration-outside-editor.md
"""
from __future__ import annotations

from typing import Any

from CortexOS.connectors import cursor_session, workspaces


def dispatch(text: str, *, kind: str | None = None) -> dict[str, Any]:
    body = (text or "").strip()
    if not body:
        raise ValueError("dispatch requires a non-empty message")
    resolved_kind = workspaces.infer_kind(body, kind)
    workspace_id = workspaces.resolve_workspace(body, kind=resolved_kind)
    ws = workspaces.get(workspace_id)
    result: dict[str, Any] = {
        "kind": resolved_kind,
        "workspace": workspace_id,
        "workspace_root": ws["root"],
        "workspace_present": bool(ws["present"]),
        "orchestrator": "cortex",
        "cursor_chat_id": None,
        "surface": "chatbot",
        "new_cursor_chat": False,
    }
    if resolved_kind == "chat":
        return result
    port = cursor_session.get_port()
    chat_id = port.open_chat(workspace_id, body)
    result["cursor_chat_id"] = chat_id
    result["surface"] = "cursor_new_chat"
    result["new_cursor_chat"] = True
    return result
