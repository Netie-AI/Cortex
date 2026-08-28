"""Constructor-style agent roster. Cortex orchestrates; each agent is a surface.

distill: skill_distill/captures/2026-08-22_cursor_orchestration-outside-editor.md
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from CortexOS.connectors import computer_control
from CortexOS.connectors.dispatch import dispatch

_ROSTER: tuple[dict[str, str], ...] = (
    {
        "id": "constructor",
        "name": "Constructor Agent",
        "workspace": "cortex",
        "icon": "C",
        "color": "#388bfd",
        "role": "Orchestrate builds across repos",
    },
    {
        "id": "verify",
        "name": "Verify Agent",
        "workspace": "cortex",
        "icon": "V",
        "color": "#f778ba",
        "role": "Gates, pytest, honesty checks",
    },
    {
        "id": "devops",
        "name": "DevOps Agent",
        "workspace": "cortex",
        "icon": "D",
        "color": "#39d353",
        "role": "CI, tickets, landings",
    },
    {
        "id": "ux",
        "name": "UX Experience Agent",
        "workspace": "chatbot",
        "icon": "U",
        "color": "#f85149",
        "role": "Chat and UI copy",
    },
    {
        "id": "pointer",
        "name": "Pointer Agent",
        "workspace": "pointer",
        "icon": "P",
        "color": "#8b949e",
        "role": "Computer control / Act",
    },
    {
        "id": "ticket",
        "name": "Ticket Runner",
        "workspace": "cortex",
        "icon": "T",
        "color": "#d29922",
        "role": "Seat work on a ticket",
    },
    {
        "id": "pr",
        "name": "PR Bot",
        "workspace": "cortex",
        "icon": "R",
        "color": "#58a6ff",
        "role": "Open and update pull requests",
    },
    {
        "id": "prd",
        "name": "PRD Agent",
        "workspace": "cortex",
        "icon": "S",
        "color": "#8b949e",
        "role": "Spec and acceptance",
    },
    {
        "id": "seo",
        "name": "SEO Exposure Agent",
        "workspace": "netie",
        "icon": "E",
        "color": "#1f6feb",
        "role": "Discovery and exposure",
    },
)

_INBOX: dict[str, list[dict[str, Any]]] = {}


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def reset_for_tests() -> None:
    _INBOX.clear()


def roster() -> list[dict[str, Any]]:
    cc = computer_control.probe()
    out: list[dict[str, Any]] = []
    for row in _ROSTER:
        msgs = _INBOX.get(row["id"]) or []
        snippet = msgs[-1]["text"] if msgs else row["role"]
        item = dict(row)
        item["snippet"] = snippet[:80]
        item["unread"] = False
        item["message_count"] = len(msgs)
        if row["id"] == "pointer":
            item["computer_control"] = {
                "armed": cc["armed"],
                "reason": cc["reason"],
            }
        out.append(item)
    return out


def get(agent_id: str) -> dict[str, str]:
    aid = (agent_id or "").strip().lower()
    for row in _ROSTER:
        if row["id"] == aid:
            return dict(row)
    raise KeyError(f"unknown agent {agent_id!r}")


def messages(agent_id: str) -> list[dict[str, Any]]:
    get(agent_id)
    return list(_INBOX.get(agent_id) or [])


def history(agent_id: str) -> list[dict[str, Any]]:
    return messages(agent_id)


def post(agent_id: str, text: str, *, kind: str | None = None) -> dict[str, Any]:
    agent = get(agent_id)
    body = (text or "").strip()
    if not body:
        raise ValueError("message required")
    kind = kind or ("chat" if agent["workspace"] == "chatbot" else "task")
    routed = dispatch(body, kind=kind, workspace=agent["workspace"])
    inbox = _INBOX.setdefault(agent_id, [])
    inbox.append({"role": "user", "text": body, "ts": _now(), "agent": agent_id})
    reply_bits = [
        f"{agent['name']} took it.",
        f"workspace={routed['workspace']} surface={routed['surface']}",
    ]
    if routed.get("cursor_chat_id"):
        reply_bits.append(f"cursor_chat={routed['cursor_chat_id']}")
    if agent_id == "pointer":
        cc = computer_control.probe()
        reply_bits.append(f"computer_control armed={cc['armed']} ({cc['reason']})")
    reply = " ".join(reply_bits)
    inbox.append({"role": "assistant", "text": reply, "ts": _now(), "agent": agent_id})
    return {
        "agent": agent,
        "dispatch": routed,
        "messages": list(inbox),
    }
