"""Minimal MCP client over stdio (newline-delimited JSON-RPC), no SDK.

This is the third-party MCP *client* the connectors probe deliberately left
parked (see ``CortexOS/connectors/computer_control.py`` - "Third-party MCP
clients stay P16"). It ships behind three gates, strictly tighter than the
probe's own: the ``CORTEX_COMPUTER_CONTROL=1`` master switch decides whether
a server process may even start, the operator must arm each server in the UI,
and mutating tools still take a per-call confirm (``policy.decide``).

Every state a server can be in is visible in ``/crew/mcp`` - starting,
ready, failed with the stderr tail, or stopped - because a dead automation
server that looks connected would burn an agent's whole step budget.
"""

from __future__ import annotations

import asyncio
import json
import os
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

PROTOCOL_VERSION = "2025-06-18"
STREAM_LIMIT = 8 * 1024 * 1024  # Windows-MCP snapshots can be megabytes
START_TIMEOUT_S = 45
CALL_TIMEOUT_S = 90

# Committed default: catalogue Windows-MCP but leave it disarmed. Arming is a
# runtime decision persisted to data/crew/mcp_servers.json, never to git.
DEFAULT_SPECS: list[dict[str, Any]] = [
    {
        "name": "uacc",
        "command": ["uv", "tool", "run", "--from", "uacc", "--with", "mcp", "uacc"],
        "cwd": None,
        "env": {"UACC_SAFE_MODE": "true", "UACC_HUMAN_MIMICRY": "false"},
        "armed": False,
    },
    {
        "name": "windows-mcp",
        "command": ["uv", "tool", "run", "--from", "windows-mcp", "windows-mcp"],
        "cwd": None,
        "env": {"ANONYMIZED_TELEMETRY": "false"},
        "armed": False,
    },
    {
        "name": "computer-control-mcp",
        "command": ["uv", "tool", "run", "--from", "computer-control-mcp", "computer-control-mcp"],
        "cwd": None,
        "env": {},
        "armed": False,
    },
]


@dataclass
class MCPServerSpec:
    name: str
    command: list[str]
    cwd: str | None = None
    env: dict[str, str] = field(default_factory=dict)
    armed: bool = False

    def public(self) -> dict[str, Any]:
        return {"name": self.name, "command": self.command, "armed": self.armed}


def load_specs(path: Path) -> list[MCPServerSpec]:
    if not path.is_file():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(DEFAULT_SPECS, indent=2), encoding="utf-8")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raw = DEFAULT_SPECS
    specs = []
    for item in raw:
        try:
            specs.append(
                MCPServerSpec(
                    name=str(item["name"]),
                    command=[str(c) for c in item["command"]],
                    cwd=item.get("cwd"),
                    env={str(k): str(v) for k, v in (item.get("env") or {}).items()},
                    armed=bool(item.get("armed", False)),
                )
            )
        except (KeyError, TypeError):
            continue
    for spec in specs:
        if spec.name == "uacc":
            spec.env.setdefault("UACC_SAFE_MODE", "true")
            spec.env.setdefault("UACC_HUMAN_MIMICRY", "false")
    return specs


def save_specs(path: Path, specs: list[MCPServerSpec]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = [
        {"name": s.name, "command": s.command, "cwd": s.cwd, "env": s.env, "armed": s.armed}
        for s in specs
    ]
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


class MCPClient:
    def __init__(self, spec: MCPServerSpec) -> None:
        self.spec = spec
        self.status = "stopped"
        self.tools: list[dict[str, Any]] = []
        self._proc: asyncio.subprocess.Process | None = None
        self._futures: dict[int, asyncio.Future[dict[str, Any]]] = {}
        self._next_id = 0
        self._reader_task: asyncio.Task[None] | None = None
        self._stderr_task: asyncio.Task[None] | None = None
        self._stderr_tail: deque[str] = deque(maxlen=50)

    def _fail(self, reason: str) -> None:
        self.status = f"failed: {reason}"
        for fut in self._futures.values():
            if not fut.done():
                fut.set_exception(RuntimeError(reason))
        self._futures.clear()

    async def start(self) -> None:
        if self._proc is not None:
            return
        self.status = "starting"
        try:
            self._proc = await asyncio.create_subprocess_exec(
                *self.spec.command,
                cwd=self.spec.cwd or None,
                env={**os.environ, **self.spec.env},
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                limit=STREAM_LIMIT,
            )
        except (OSError, ValueError) as exc:
            self._fail(f"spawn: {exc}")
            return
        self._reader_task = asyncio.create_task(self._read_stdout())
        self._stderr_task = asyncio.create_task(self._read_stderr())
        try:
            init = await asyncio.wait_for(
                self._request(
                    "initialize",
                    {
                        "protocolVersion": PROTOCOL_VERSION,
                        "capabilities": {"tools": {}},
                        "clientInfo": {"name": "cortex-crew", "version": "0.1.0"},
                    },
                ),
                timeout=START_TIMEOUT_S,
            )
            await self._notify("notifications/initialized", {})
            listing = await asyncio.wait_for(
                self._request("tools/list", {}), timeout=START_TIMEOUT_S
            )
            self.tools = list(listing.get("tools") or [])
            server_info = init.get("serverInfo") or {}
            self.status = f"ready ({server_info.get('name', 'unknown')}, {len(self.tools)} tools)"
        except (TimeoutError, RuntimeError, OSError) as exc:
            tail = " | ".join(list(self._stderr_tail)[-3:])
            self._fail(f"handshake: {exc} {('[' + tail + ']') if tail else ''}".strip())
            await self.stop()

    async def call(self, tool: str, args: dict[str, Any], timeout: int = CALL_TIMEOUT_S) -> str:
        if self._proc is None or not self.status.startswith("ready"):
            raise RuntimeError(f"MCP server '{self.spec.name}' is not ready ({self.status})")
        result = await asyncio.wait_for(
            self._request("tools/call", {"name": tool, "arguments": args}), timeout=timeout
        )
        parts: list[str] = []
        for block in result.get("content") or []:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(str(block.get("text", "")))
            elif isinstance(block, dict):
                parts.append(f"[{block.get('type', 'block')}]")
        text = "\n".join(p for p in parts if p) or "(empty result)"
        if result.get("isError"):
            return f"TOOL ERROR: {text}"
        return text

    async def stop(self) -> None:
        proc, self._proc = self._proc, None
        for task in (self._reader_task, self._stderr_task):
            if task is not None:
                task.cancel()
        self._reader_task = self._stderr_task = None
        if proc is not None:
            try:
                proc.terminate()
            except ProcessLookupError:
                pass
            try:
                await asyncio.wait_for(proc.wait(), timeout=5)
            except TimeoutError:
                proc.kill()
        if self.status.startswith(("ready", "starting")):
            self.status = "stopped"
        self.tools = []

    async def _request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        self._next_id += 1
        req_id = self._next_id
        fut: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        self._futures[req_id] = fut
        await self._send({"jsonrpc": "2.0", "id": req_id, "method": method, "params": params})
        try:
            return await fut
        finally:
            self._futures.pop(req_id, None)

    async def _notify(self, method: str, params: dict[str, Any]) -> None:
        await self._send({"jsonrpc": "2.0", "method": method, "params": params})

    async def _send(self, obj: dict[str, Any]) -> None:
        proc = self._proc
        if proc is None or proc.stdin is None:
            raise RuntimeError("server process is gone")
        proc.stdin.write((json.dumps(obj, ensure_ascii=True) + "\n").encode("utf-8"))
        await proc.stdin.drain()

    async def _read_stdout(self) -> None:
        proc = self._proc
        if proc is None or proc.stdout is None:
            return
        try:
            while True:
                line = await proc.stdout.readline()
                if not line:
                    self._fail("server exited")
                    return
                try:
                    msg = json.loads(line.decode("utf-8", "replace"))
                except ValueError:
                    continue  # non-protocol noise on stdout
                msg_id = msg.get("id")
                fut = self._futures.get(msg_id) if msg_id is not None else None
                if fut is None or fut.done():
                    continue  # notification or stale reply
                if "error" in msg:
                    err = msg["error"] or {}
                    fut.set_exception(
                        RuntimeError(f"rpc {err.get('code')}: {err.get('message', 'error')}")
                    )
                else:
                    fut.set_result(msg.get("result") or {})
        except asyncio.CancelledError:
            return

    async def _read_stderr(self) -> None:
        proc = self._proc
        if proc is None or proc.stderr is None:
            return
        try:
            while True:
                line = await proc.stderr.readline()
                if not line:
                    return
                self._stderr_tail.append(line.decode("utf-8", "replace").rstrip())
        except asyncio.CancelledError:
            return


class MCPManager:
    """Owns one client per catalogued server; arming starts, disarming stops."""

    def __init__(self, config_path: Path, master_on: bool) -> None:
        self.config_path = config_path
        self.master_on = master_on
        self.clients: dict[str, MCPClient] = {}
        for spec in load_specs(config_path):
            self.clients[spec.name] = MCPClient(spec)

    async def start_armed(self) -> None:
        if not self.master_on:
            return
        for client in self.clients.values():
            if client.spec.armed:
                await client.start()

    async def stop_all(self) -> None:
        for client in self.clients.values():
            await client.stop()

    async def arm(self, name: str, armed: bool) -> MCPClient:
        client = self.clients.get(name)
        if client is None:
            raise KeyError(name)
        if armed and not self.master_on:
            raise PermissionError(
                "computer control is off: set CORTEX_COMPUTER_CONTROL=1 and restart the crew server"
            )
        client.spec.armed = armed
        save_specs(self.config_path, [c.spec for c in self.clients.values()])
        if armed:
            await client.start()
        else:
            await client.stop()
        return client

    def status(self) -> list[dict[str, Any]]:
        out = []
        for client in self.clients.values():
            status = client.status
            if not self.master_on and not status.startswith("failed"):
                status = "off (CORTEX_COMPUTER_CONTROL not set)"
            out.append(
                {
                    "name": client.spec.name,
                    "status": status,
                    "armed": client.spec.armed,
                    "enabled": self.master_on,
                    "tools": [
                        {
                            "name": t.get("name", ""),
                            "description": (t.get("description") or "")[:200],
                        }
                        for t in client.tools
                    ],
                }
            )
        return out

    def tool_catalog(self) -> list[tuple[str, dict[str, Any]]]:
        """(server_name, tool_dict) for every tool on every ready server."""
        out: list[tuple[str, dict[str, Any]]] = []
        for client in self.clients.values():
            if client.status.startswith("ready"):
                out.extend((client.spec.name, tool) for tool in client.tools)
        return out
