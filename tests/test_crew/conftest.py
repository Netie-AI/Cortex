"""Fixtures for crew tests - no network, no real model, no real desktop.

The fake LLM routes scripts by charter (manager vs teammate system prompt) so
concurrent agent loops stay deterministic. Every test asserts on what the
operator would see - stored transcript messages and statuses - per the
answer-path law (CLAUDE.md section 8): intermediate artifacts alone certify
nothing.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from CortexOS.crew.config import CrewSettings
from CortexOS.crew.events import EventBus
from CortexOS.crew.llm import LLMResult
from CortexOS.crew.mcp_client import MCPManager
from CortexOS.crew.runtime import CrewRuntime
from CortexOS.crew.store import CrewStore


@pytest.fixture()
def crew_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """A deterministic provider environment: explicit model, no probes."""
    for var in (
        "ANTHROPIC_API_KEY",
        "OPENROUTER_API_KEY",
        "DEEPSEEK_API_KEY",
        "OPENAI_API_KEY",
        "CREW_OPENAI_BASE_URL",
        "CURSOR_API_KEY",
        "XAI_API_KEY",
        "GMAIL_IMAP_USER",
        "GMAIL_APP_PASSWORD",
        "GROQ_API_KEY",
        "GOOGLE_API_KEY",
        "CEREBRAS_API_KEY",
        "MISTRAL_API_KEY",
        "CREW_PROVIDER",
    ):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("CREW_MODEL", "test/fake-model")
    monkeypatch.setenv("CREW_ALLOW_OLLAMA", "0")
    monkeypatch.setenv("CREW_OPENVAULT", "0")
    monkeypatch.setenv("CREW_LIVE_PROBES", "0")
    from CortexOS.crew.llm import reset_usage

    reset_usage()


@pytest.fixture()
def settings(tmp_path: Path) -> CrewSettings:
    from CortexOS.crew.board import ensure_skill_packs

    data_dir = tmp_path / "crew"
    ensure_skill_packs(data_dir / "skills")
    return CrewSettings(
        port=0,
        engine_url="http://127.0.0.1:9",  # closed port: engine visibly offline
        engine_session="demo",
        data_dir=data_dir,
        master_computer_control=False,
        confirm_timeout_s=5,
        llm_timeout_s=5,
    )


class FakeLLM:
    """Scripted completions; manager and teammate scripts are separate queues."""

    def __init__(self) -> None:
        self.manager: list[LLMResult] = []
        self.teammate: list[LLMResult] = []
        self.calls: list[dict[str, Any]] = []

    async def __call__(
        self,
        model: str,
        messages: list[dict[str, Any]],
        *,
        tools: Any = None,
        api_base: Any = None,
        timeout: int = 0,
        max_tokens: int = 0,
        stream_cb: Any = None,
    ) -> LLMResult:
        self.calls.append({"model": model, "messages": messages, "tools": tools})
        head = str(messages[0].get("content", "")) if messages else ""
        queue = self.manager if head.startswith("You are the Manager") else self.teammate
        if queue:
            return queue.pop(0)
        return LLMResult(text="(no script)", model=model)


class FakeBridge:
    base_url = "http://fake-engine"

    def __init__(self) -> None:
        self.asked: list[str] = []

    async def ask(self, question: str) -> dict[str, Any]:
        self.asked.append(question)
        return {
            "ok": True,
            "answer": "42 units on hand",
            "badge": "governed",
            "route": "metric",
            "audit_id": "audit-1",
            "row_count": 1,
        }

    async def health(self) -> dict[str, Any]:
        return {"ok": True, "url": self.base_url}


class FakeMCPClient:
    """Stand-in for MCPClient. Mirrors the whole interface the manager uses,
    including idle suspension - a double that is missing a method the real
    client has turns into a crashed run rather than a failed assertion."""

    def __init__(self, name: str, tools: list[dict[str, Any]], armed: bool = True) -> None:
        self.spec = SimpleNamespace(name=name, armed=armed, command=[], env={}, cwd=None)
        self.status = f"ready (fake, {len(tools)} tools)"
        self.tools = tools
        self.known_tools = list(tools)
        self.suspended = False
        self.idle_s = 0.0
        self.last_used = 0.0
        self.called: list[tuple[str, dict[str, Any]]] = []
        self.starts = 0

    def offered_tools(self) -> list[dict[str, Any]]:
        if self.status.startswith("ready"):
            return self.tools
        if self.suspended:
            return self.known_tools
        return []

    async def call(self, tool: str, args: dict[str, Any], timeout: int = 90) -> str:
        if self.suspended or not self.status.startswith("ready"):
            raise RuntimeError(f"MCP server '{self.spec.name}' is not ready ({self.status})")
        self.called.append((tool, args))
        return f"ok-{tool}"

    async def start(self) -> None:
        self.starts += 1
        self.suspended = False
        self.status = f"ready (fake, {len(self.known_tools)} tools)"
        self.tools = list(self.known_tools)

    async def stop(self) -> None:
        self.suspended = False
        self.status = "stopped"
        self.tools = []

    async def suspend(self) -> None:
        self.suspended = True
        self.status = "suspended (idle; starts on the next call)"
        self.tools = []


@pytest.fixture()
def rig(crew_env: None, settings: CrewSettings) -> SimpleNamespace:
    store = CrewStore(settings.db_path)
    bus = EventBus()
    mcp = MCPManager(settings.mcp_config_path, master_on=settings.master_computer_control)
    bridge = FakeBridge()
    fake_llm = FakeLLM()
    runtime = CrewRuntime(store, bus, settings, mcp, bridge, llm_chat=fake_llm)
    return SimpleNamespace(
        store=store, bus=bus, mcp=mcp, bridge=bridge, llm=fake_llm, runtime=runtime,
        settings=settings,
    )


async def wait_run_done(runtime: CrewRuntime, space_id: str, timeout: float = 10.0) -> None:
    """Wait until the space has no active run; fail loudly on timeout."""
    deadline = asyncio.get_running_loop().time() + timeout
    while runtime._space_run.get(space_id):
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError("run did not finish in time")
        await asyncio.sleep(0.01)
