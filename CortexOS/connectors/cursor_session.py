"""Cursor session port — retrieve, instruct, open a new chat.

The engine holds the port. A local sidecar may register later via
CORTEX_CURSOR_BRIDGE_URL. This process cannot click the Cursor sidebar.

distill: skill_distill/captures/2026-08-22_cursor_orchestration-outside-editor.md
"""
from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

from CortexOS.paths import data_path

_STORE: dict[str, dict[str, Any]] = {}
_PATH_OVERRIDE: Path | None = None


class CursorSessionPort(Protocol):
    def open_chat(self, workspace: str, task: str) -> str: ...
    def instruct(self, chat_id: str, instruction: str) -> dict[str, Any]: ...
    def messages(self, chat_id: str) -> list[dict[str, Any]]: ...
    def list_chats(self) -> list[dict[str, Any]]: ...


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _store_path() -> Path:
    if _PATH_OVERRIDE is not None:
        return _PATH_OVERRIDE
    raw = (os.environ.get("CORTEX_CURSOR_SESSIONS_PATH") or "").strip()
    if raw:
        return Path(raw)
    return data_path("connectors", "cursor_sessions.json")


def _load() -> dict[str, dict[str, Any]]:
    path = _store_path()
    if not path.is_file():
        return dict(_STORE)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return dict(_STORE)
    if isinstance(raw, dict):
        return {str(k): dict(v) for k, v in raw.items() if isinstance(v, dict)}
    return dict(_STORE)


def _save(store: dict[str, dict[str, Any]]) -> None:
    global _STORE
    _STORE = dict(store)
    path = _store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(store, indent=2), encoding="utf-8")


def reset_for_tests(path: Path | None = None) -> None:
    global _STORE, _PATH_OVERRIDE
    _STORE = {}
    _PATH_OVERRIDE = path
    if path is not None and path.exists():
        path.unlink()


class MemoryCursorSession:
    """In-process store. Does not drive the Cursor GUI."""

    def open_chat(self, workspace: str, task: str) -> str:
        store = _load()
        chat_id = "cch_" + uuid.uuid4().hex[:12]
        store[chat_id] = {
            "id": chat_id,
            "workspace": workspace,
            "task": task,
            "status": "open",
            "created_at": _now(),
            "messages": [
                {"role": "user", "text": task, "ts": _now()},
            ],
        }
        _save(store)
        return chat_id

    def instruct(self, chat_id: str, instruction: str) -> dict[str, Any]:
        store = _load()
        chat = store.get(chat_id)
        if chat is None:
            raise KeyError(f"unknown cursor chat {chat_id}")
        chat.setdefault("messages", []).append(
            {"role": "user", "text": instruction, "ts": _now()}
        )
        chat["status"] = "instructed"
        _save(store)
        return dict(chat)

    def messages(self, chat_id: str) -> list[dict[str, Any]]:
        store = _load()
        chat = store.get(chat_id)
        if chat is None:
            raise KeyError(f"unknown cursor chat {chat_id}")
        return list(chat.get("messages") or [])

    def list_chats(self) -> list[dict[str, Any]]:
        store = _load()
        out: list[dict[str, Any]] = []
        for chat in store.values():
            row = {k: v for k, v in chat.items() if k != "messages"}
            row["message_count"] = len(chat.get("messages") or [])
            out.append(row)
        out.sort(key=lambda r: str(r.get("created_at") or ""), reverse=True)
        return out


_PORT: CursorSessionPort = MemoryCursorSession()


def get_port() -> CursorSessionPort:
    return _PORT


def set_port_for_tests(port: CursorSessionPort | None) -> None:
    global _PORT
    _PORT = port if port is not None else MemoryCursorSession()
