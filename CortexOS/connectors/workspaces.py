"""Named workspaces Cortex can dispatch into.

Windows desktop defaults are documented, never required. Override with
CORTEX_WS_<ID>. Missing disks stay honest: present=false.

distill: skill_distill/captures/2026-08-22_cursor_orchestration-outside-editor.md
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from CortexOS.paths import repo_root

# id -> (role, env var, Windows default, what it is)
_CATALOG: tuple[tuple[str, str, str, str, str], ...] = (
    ("cortex", "engine", "CORTEX_WS_CORTEX", r"D:\Cortex", "Cortex engine (this repo)"),
    ("netie", "orchestration_kb", "CORTEX_WS_NETIE", r"D:\Netie", "Netie KB / orchestration"),
    ("dms", "vertical", "CORTEX_WS_DMS", r"D:\DMS", "DMS consumer vertical"),
    ("chatbot", "normal_chat", "CORTEX_WS_CHATBOT", r"D:\chatbot", "Normal chat product"),
    ("pointer", "computer_control", "CORTEX_WS_POINTER", r"D:\Pointer", "Pointer / Act computer-control client"),
    ("omi", "router", "CORTEX_WS_OMI", r"D:\OMI", "OMI router"),
    ("openvault", "custody", "CORTEX_WS_OPENVAULT", r"D:\OpenVault", "OpenVault key custody"),
)

_DMS_HINTS = ("dms", "warehouse", "sku", "shipment", "supplier", "inventory")
_NETIE_HINTS = ("netie", "distill", "kb", "skill_distill")
_CHAT_HINTS = ("chatbot", "customer chat", "normal chat")
_POINTER_HINTS = ("pointer", "computer control", "pyautogui", "uacc", "windows-mcp")
_OMI_HINTS = ("omi", "omii")
_VAULT_HINTS = ("openvault", "key vault")


def _root_for(env_name: str, windows_default: str, workspace_id: str) -> Path:
    raw = (os.environ.get(env_name) or "").strip()
    if raw:
        return Path(raw)
    if workspace_id == "cortex":
        return repo_root()
    return Path(windows_default)


def catalog() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for wid, role, env_name, win_default, blurb in _CATALOG:
        root = _root_for(env_name, win_default, wid)
        rows.append(
            {
                "id": wid,
                "role": role,
                "root": str(root),
                "present": root.exists(),
                "env": env_name,
                "windows_default": win_default,
                "blurb": blurb,
            }
        )
    return rows


def get(workspace_id: str) -> dict[str, Any]:
    wid = (workspace_id or "").strip().lower()
    for row in catalog():
        if row["id"] == wid:
            return row
    raise KeyError(f"unknown workspace {workspace_id!r}")


def resolve_workspace(text: str, *, kind: str) -> str:
    """Pick a workspace. Normal chat always lands on chatbot."""
    if kind == "chat":
        return "chatbot"
    q = (text or "").lower()
    if any(h in q for h in _CHAT_HINTS):
        return "chatbot"
    if any(h in q for h in _POINTER_HINTS):
        return "pointer"
    if any(h in q for h in _OMI_HINTS):
        return "omi"
    if any(h in q for h in _VAULT_HINTS):
        return "openvault"
    if any(h in q for h in _DMS_HINTS):
        return "dms"
    if any(h in q for h in _NETIE_HINTS):
        return "netie"
    return "cortex"


def infer_kind(text: str, explicit: str | None = None) -> str:
    if explicit in ("chat", "task"):
        return explicit
    q = (text or "").lower()
    if any(w in q for w in ("new task", "ship ", "fix ", "build ", "implement ")):
        return "task"
    if any(w in q for w in ("just chatting", "normal chat", "say hi")):
        return "chat"
    return "task"
