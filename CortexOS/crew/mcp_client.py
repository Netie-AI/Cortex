"""Stdio MCP client for a fixed allowlist of local desktop-control servers.

This is deliberately not a general third-party MCP loader: the servers it will
spawn are named here, resolved to local executables, and each exposes only an
allowlisted subset of its tools to agents. Shell, registry, file-system and
process tools are excluded by policy — the exclusion is reported in
``status()`` so the narrowing is visible, never silent (R-0011).
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
from pathlib import Path
from typing import Any

PROTOCOL_VERSION = "2025-06-18"

# Read-or-aim UI tools only. PowerShell / Registry / FileSystem / Process /
# MultiEdit / MultiSelect / Notification stay blocked until a per-action
# approval flow exists.
SERVER_TOOL_ALLOWLIST: dict[str, set[str]] = {
    "windows-mcp": {
        "Snapshot",
        "Screenshot",
        "DisplayInventory",
        "Click",
        "Type",
        "Scroll",
        "Move",
        "Shortcut",
        "Wait",
        "WaitFor",
        "App",
        "Clipboard",
        "Scrape",
    },
    "computer-control-mcp": {
        "take_screenshot",
        "take_screenshot_with_ocr",
        "get_screen_size",
        "list_windows",
        "activate_window",
        "click_screen",
        "move_mouse",
        "drag_mouse",
        "type_text",
        "press_key",
        "press_keys",
        "wait_milliseconds",
    },
}


def _local_bin(name: str) -> str | None:
    found = shutil.which(name)
    if found:
        return found
    candidate = Path.home() / ".local" / "bin" / f"{name}.exe"
    return str(candidate) if candidate.exists() else None


def known_servers() -> dict[str, list[str] | None]:
    """Name -> spawn command (None when the executable is not installed)."""
    servers: dict[str, list[str] | None] = {}
    wm = _local_bin("windows-mcp")
    servers["windows-mcp"] = [wm, "serve"] if wm else None
    cc = _local_bin("computer-control-mcp")
    servers["computer-control-mcp"] = [cc] if cc else None
    return servers


class McpStdioClient:
    """One spawned MCP server over stdio, JSON-RPC framed line-by-line."""

    def __init__(self, name: str, command: list[str]):
        self.name = name
        self.command = command
        self.proc: asyncio.subprocess.Process | None = None
        self._next_id = 0
        self._pending: dict[int, asyncio.Future[dict[str, Any]]] = {}
        self._reader_task: asyncio.Task[None] | None = None
        self.tools: list[dict[str, Any]] = []
        self.blocked: list[str] = []

    async def start(self, timeout: float = 60.0) -> None:
        env = dict(os.environ)
        env.setdefault("ANONYMIZED_TELEMETRY", "false")
        self.proc = await asyncio.create_subprocess_exec(
            *self.command,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            env=env,
        )
        self._reader_task = asyncio.create_task(self._read_loop())
        init = await self._request(
            "initialize",
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "cortex-crew", "version": "0.1"},
            },
            timeout=timeout,
        )
        if "result" not in init:
            raise RuntimeError(f"{self.name}: initialize failed: {init}")
        await self._notify("notifications/initialized")
        listed = await self._request("tools/list", {}, timeout=timeout)
        allow = SERVER_TOOL_ALLOWLIST.get(self.name, set())
        all_tools = listed.get("result", {}).get("tools", [])
        self.tools = [t for t in all_tools if t.get("name") in allow]
        self.blocked = sorted(
            t.get("name", "?") for t in all_tools if t.get("name") not in allow
        )

    async def call_tool(
        self, name: str, arguments: dict[str, Any], timeout: float = 120.0
    ) -> dict[str, Any]:
        if name not in {t.get("name") for t in self.tools}:
            return {"ok": False, "error": f"tool '{name}' is not allowlisted on {self.name}"}
        resp = await self._request(
            "tools/call", {"name": name, "arguments": arguments}, timeout=timeout
        )
        if "error" in resp:
            return {"ok": False, "error": str(resp["error"])[:500]}
        result = resp.get("result", {})
        texts = [
            c.get("text", "")
            for c in result.get("content") or []
            if c.get("type") == "text"
        ]
        return {
            "ok": not result.get("isError", False),
            "text": "\n".join(texts)[:8000],
        }

    @property
    def alive(self) -> bool:
        return self.proc is not None and self.proc.returncode is None

    async def stop(self) -> None:
        if self._reader_task:
            self._reader_task.cancel()
        if self.proc and self.proc.returncode is None:
            self.proc.kill()
            try:
                await asyncio.wait_for(self.proc.wait(), timeout=5)
            except (TimeoutError, asyncio.TimeoutError):
                pass

    async def _read_loop(self) -> None:
        assert self.proc and self.proc.stdout
        while True:
            line = await self.proc.stdout.readline()
            if not line:
                break
            try:
                msg = json.loads(line.decode("utf-8", "replace"))
            except ValueError:
                continue
            mid = msg.get("id")
            fut = self._pending.pop(mid, None) if mid is not None else None
            if fut is not None and not fut.done():
                fut.set_result(msg)

    async def _request(
        self, method: str, params: dict[str, Any], timeout: float
    ) -> dict[str, Any]:
        assert self.proc and self.proc.stdin
        self._next_id += 1
        mid = self._next_id
        fut: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        self._pending[mid] = fut
        payload = {"jsonrpc": "2.0", "id": mid, "method": method, "params": params}
        self.proc.stdin.write((json.dumps(payload) + "\n").encode("utf-8"))
        await self.proc.stdin.drain()
        try:
            return await asyncio.wait_for(fut, timeout=timeout)
        except (TimeoutError, asyncio.TimeoutError):
            self._pending.pop(mid, None)
            return {"error": {"message": f"timeout after {timeout}s calling {method}"}}

    async def _notify(self, method: str) -> None:
        assert self.proc and self.proc.stdin
        payload = {"jsonrpc": "2.0", "method": method}
        self.proc.stdin.write((json.dumps(payload) + "\n").encode("utf-8"))
        await self.proc.stdin.drain()


class McpBridge:
    """Lazily connects the allowlisted servers and routes tool calls."""

    def __init__(self) -> None:
        self.clients: dict[str, McpStdioClient] = {}
        self.errors: dict[str, str] = {}

    async def ensure(self, server: str) -> McpStdioClient | None:
        client = self.clients.get(server)
        if client is not None and client.alive:
            return client
        command = known_servers().get(server)
        if not command:
            self.errors[server] = "executable not installed"
            return None
        client = McpStdioClient(server, command)
        try:
            await client.start()
        except Exception as exc:  # noqa: BLE001 — surfaced in status(), never silent
            self.errors[server] = str(exc)[:300]
            await client.stop()
            return None
        self.errors.pop(server, None)
        self.clients[server] = client
        return client

    async def call(
        self, server: str, tool: str, arguments: dict[str, Any]
    ) -> dict[str, Any]:
        client = await self.ensure(server)
        if client is None:
            return {
                "ok": False,
                "error": f"{server} unavailable: {self.errors.get(server, 'unknown')}",
            }
        return await client.call_tool(tool, arguments)

    async def status(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for name, command in known_servers().items():
            client = self.clients.get(name)
            out[name] = {
                "installed": command is not None,
                "connected": bool(client and client.alive),
                "tools": [t.get("name") for t in client.tools] if client else [],
                "blocked": client.blocked if client else [],
                "error": self.errors.get(name),
            }
        return out

    async def stop_all(self) -> None:
        for client in self.clients.values():
            await client.stop()
        self.clients.clear()
