"""Standing / session / one-off approval ladder. Server source of truth.

Chrome Settings used to keep this in localStorage, so a mutating tool could
skip the operator while the process itself still treated every call as
one-off. The book is the crew-wide floor: per-agent grants may only tighten
it (see ``policy.decide``). Standing and tripped circuits persist under
``data/crew/approvals.json``; session grants die with the process.
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any

MODE_ONE_OFF = "one-off"
MODE_SESSION = "session"
MODE_STANDING = "standing"
MODES = frozenset({MODE_ONE_OFF, MODE_SESSION, MODE_STANDING})
CIRCUIT_TRIPS = 3
LAW = (
    "Ladder: one-off, then session, then standing allowlist. Revocable. "
    "Repeated deny trips the circuit-breaker. Per-agent grants only tighten "
    "crew policy."
)


def parse_mode(raw: str | None) -> str:
    value = (raw or "").strip().lower().replace("_", "-")
    if value in MODES:
        return value
    raise ValueError(f"unknown ladder mode '{raw}'")


def tool_names(*parts: str) -> frozenset[str]:
    out: set[str] = set()
    for raw in parts:
        text = str(raw or "").strip()
        if not text:
            continue
        out.add(text)
        if "." in text:
            out.add(text.split(".", 1)[-1])
    return frozenset(out)


class ApprovalBook:
    """Crew-wide ladder. Empty path keeps the book in memory (tests)."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path
        self._lock = threading.Lock()
        self._mode = MODE_ONE_OFF
        self._allow: dict[str, str] = {}
        self._session: dict[str, str] = {}
        self._circuit: dict[str, int] = {}
        self._streak: dict[str, int] = {}
        self._load()

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "mode": self._mode,
                "allow": dict(self._allow),
                "session": dict(self._session),
                "circuit": dict(self._circuit),
                "law": LAW,
            }

    def set_mode(self, mode: str) -> dict[str, Any]:
        parsed = parse_mode(mode)
        with self._lock:
            self._mode = parsed
            self._persist()
        return self.snapshot()

    def revoke(self, tool: str | None = None) -> dict[str, Any]:
        with self._lock:
            if tool:
                keys = tool_names(tool)
                for key in keys:
                    self._allow.pop(key, None)
                    self._session.pop(key, None)
                    self._circuit.pop(key, None)
                    self._streak.pop(key, None)
            else:
                self._allow.clear()
                self._session.clear()
                self._circuit.clear()
                self._streak.clear()
                self._mode = MODE_ONE_OFF
            self._persist()
        return self.snapshot()

    def record_approve(self, tool: str) -> dict[str, Any]:
        key = str(tool or "").strip()
        if not key:
            return self.snapshot()
        with self._lock:
            for name in tool_names(key):
                self._streak.pop(name, None)
                self._circuit.pop(name, None)
            if self._mode == MODE_SESSION:
                self._session[key] = MODE_SESSION
            elif self._mode == MODE_STANDING:
                self._allow[key] = MODE_STANDING
            self._persist()
        return self.snapshot()

    def record_deny(self, tool: str) -> dict[str, Any]:
        key = str(tool or "").strip()
        if not key:
            return self.snapshot()
        with self._lock:
            n = int(self._streak.get(key) or 0) + 1
            self._streak[key] = n
            if n >= CIRCUIT_TRIPS:
                self._circuit[key] = n
                for name in tool_names(key):
                    self._allow.pop(name, None)
                    self._session.pop(name, None)
                self._allow.pop(key, None)
                self._session.pop(key, None)
                self._mode = MODE_ONE_OFF
                self._persist()
        return self.snapshot()

    def broken(self, tool: str) -> bool:
        keys = tool_names(tool)
        with self._lock:
            return any(name in self._circuit for name in keys)

    def covers(self, *parts: str) -> str | None:
        keys = tool_names(*parts)
        with self._lock:
            if any(name in self._circuit for name in keys):
                return None
            if any(name in self._allow for name in keys):
                return MODE_STANDING
            if any(name in self._session for name in keys):
                return MODE_SESSION
        return None

    def _load(self) -> None:
        path = self.path
        if path is None or not path.is_file():
            return
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if not isinstance(raw, dict):
            return
        try:
            self._mode = parse_mode(str(raw.get("mode") or MODE_ONE_OFF))
        except ValueError:
            self._mode = MODE_ONE_OFF
        self._allow = _str_map(raw.get("allow"), MODE_STANDING)
        self._circuit = _int_map(raw.get("circuit"))

    def _persist(self) -> None:
        path = self.path
        if path is None:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "mode": self._mode,
            "allow": dict(self._allow),
            "circuit": dict(self._circuit),
        }
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass


def _str_map(raw: Any, default: str) -> dict[str, str]:
    if not isinstance(raw, dict):
        return {}
    out: dict[str, str] = {}
    for key, value in raw.items():
        name = str(key).strip()
        if not name:
            continue
        text = str(value or "").strip() or default
        out[name] = text
    return out


def _int_map(raw: Any) -> dict[str, int]:
    if not isinstance(raw, dict):
        return {}
    out: dict[str, int] = {}
    for key, value in raw.items():
        name = str(key).strip()
        if not name:
            continue
        try:
            out[name] = int(value)
        except (TypeError, ValueError):
            continue
    return out
