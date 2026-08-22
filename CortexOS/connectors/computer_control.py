"""Computer-control drivers Cortex can arm. Default OFF.

UACC (`pip install uacc`), computer-control-mcp (PyAutoGUI + RapidOCR), and
Windows-MCP (CursorTouch, Windows only) are catalogued. This process does not
click the desktop unless CORTEX_COMPUTER_CONTROL=1 and a driver imports.
Third-party MCP *clients* stay P16. This is a first-party probe + gated invoke.

distill: skill_distill/captures/2026-08-22_cursor_orchestration-outside-editor.md
"""
from __future__ import annotations

import importlib
import os
import sys
from typing import Any

_ALLOWED_ACTIONS = frozenset({"status", "screenshot", "click", "type", "move"})

_DRIVERS: tuple[tuple[str, str | None, str, str, str], ...] = (
    (
        "uacc",
        "uacc",
        "pip install uacc",
        "https://pypi.org/project/uacc/",
        "Universal AI Computer Control (MCP). Cross-platform; pywinauto is Windows-only.",
    ),
    (
        "computer-control-mcp",
        "computer_control_mcp",
        "pip install computer-control-mcp",
        "https://github.com/AB498/computer-control-mcp",
        "PyAutoGUI + RapidOCR + ONNXRuntime (mcp.so / mcpmarket computer-control).",
    ),
    (
        "windows-mcp",
        None,
        "https://github.com/CursorTouch/Windows-MCP",
        "https://github.com/CursorTouch/Windows-MCP",
        "CursorTouch Windows-MCP. Windows desktop only.",
    ),
)


def _flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes", "on")


def _enabled() -> bool:
    return _flag("CORTEX_COMPUTER_CONTROL")


def probe() -> dict[str, Any]:
    drivers: list[dict[str, Any]] = []
    for did, module, install, url, blurb in _DRIVERS:
        imported = False
        if module:
            try:
                importlib.import_module(module)
                imported = True
            except Exception:  # noqa: BLE001 — probe must never crash the UI
                imported = False
        windows_only = did == "windows-mcp"
        platform_ok = (sys.platform == "win32") if windows_only else True
        drivers.append(
            {
                "id": did,
                "imported": imported,
                "install": install,
                "url": url,
                "blurb": blurb,
                "windows_only": windows_only,
                "platform_ok": platform_ok,
            }
        )
    imported_ok = any(d["imported"] and d["platform_ok"] for d in drivers)
    uacc_importable = any(d["id"] == "uacc" and d["imported"] for d in drivers)
    enabled = _enabled()
    reason = "computer control is off (set CORTEX_COMPUTER_CONTROL=1)"
    if enabled and not imported_ok:
        reason = (
            "flag is on but no driver imported. "
            "pip install uacc  OR  pip install computer-control-mcp. "
            "Windows-MCP only runs on Windows."
        )
    if enabled and imported_ok:
        reason = "armed"
    if sys.platform != "win32":
        extra = " This host is not Windows; Windows-MCP cannot attach."
        if "Windows-MCP" not in reason:
            reason = reason + extra
    return {
        "enabled": enabled,
        "armed": bool(enabled and imported_ok),
        "can_control": bool(enabled and imported_ok),
        "uacc_importable": uacc_importable,
        "platform": sys.platform,
        "reason": reason,
        "preset": "computer_control",
        "drivers": drivers,
        "p16": "third-party MCP clients stay parked; this is a first-party probe",
    }


def invoke(action: str, **payload: Any) -> dict[str, Any]:
    """Fail closed. Never a keylogger. Mouse/keyboard only when armed + execute."""
    act = (action or "").strip().lower()
    if act not in _ALLOWED_ACTIONS:
        return {"ok": False, "error": "action_not_allowed", "action": action}
    status = probe()
    if act == "status":
        return {"ok": True, "action": "status", "executed": False, "status": status}
    if not status["enabled"] or not status["armed"]:
        return {
            "ok": False,
            "action": act,
            "executed": False,
            "error": "not_armed",
            "reason": status["reason"],
            "status": status,
        }
    # Drivers are present and the flag is on. Still refuse to move input on a
    # headless/cloud agent unless CORTEX_COMPUTER_CONTROL_EXECUTE=1.
    if not _flag("CORTEX_COMPUTER_CONTROL_EXECUTE"):
        return {
            "ok": True,
            "action": act,
            "executed": False,
            "payload": dict(payload),
            "reason": "armed but execute is off (CORTEX_COMPUTER_CONTROL_EXECUTE=1 to move input)",
        }
    return {
        "ok": False,
        "action": act,
        "executed": False,
        "error": "sidecar_required",
        "reason": (
            "direct UACC/PyAutoGUI execute is not wired in-process; "
            "run uacc-mcp or Windows-MCP as a local sidecar"
        ),
        "payload": dict(payload),
    }
