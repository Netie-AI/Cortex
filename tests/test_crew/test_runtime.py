"""Runtime turns with a scripted fake model - no network, no real desktop.

Every test asserts on the stored transcript and statuses the operator would
see (CLAUDE.md section 8): SQL/tool internals alone certify nothing.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from CortexOS.crew.events import EventBus
from CortexOS.crew.llm import LLMError, LLMResult, ToolCall
from CortexOS.crew.mcp_client import MCPManager
from CortexOS.crew.runtime import CrewRuntime
from CortexOS.crew.store import CrewStore
from tests.test_crew.conftest import FakeBridge, FakeLLM, FakeMCPClient, wait_run_done

pytestmark = pytest.mark.asyncio


def _tc(tool: str, **args: object) -> ToolCall:
    return ToolCall(id=f"c-{tool}", name=tool, args=dict(args))


async def test_manager_reply_is_in_the_transcript(rig) -> None:
    space = rig.store.create_space("HQ")
    rig.llm.manager.append(LLMResult(text="Hello, operator.", model="gemini-3.5-flash"))
    await rig.runtime.on_user_message(space["id"], "hello")
    await wait_run_done(rig.runtime, space["id"])
    msgs = rig.store.list_messages(space["id"])
    roles = [m["role"] for m in msgs]
    assert roles == ["user", "assistant"]
    assert msgs[-1]["content"] == "Hello, operator."
    assert msgs[-1]["meta"]["provider"] == "explicit"
    assert msgs[-1]["meta"]["model"] == "gemini-3.5-flash"
    assert msgs[-1]["meta"]["route"] == "test/fake-model"
    head = rig.llm.calls[0]["messages"][0]
    assert head["content"].startswith("You are the Manager")
    assert head.get("cache_control") == {"type": "ephemeral"}
    assert "Current crew:" in rig.llm.calls[0]["messages"][1]["content"]


async def test_spawn_specialist_fills_role_and_a2a_is_visible(rig) -> None:
    space = rig.store.create_space("Build")
    rig.llm.manager.extend(
        [
            LLMResult(
                tool_calls=[
                    _tc("spawn_agent", name="PRD", brief="write the PRD for crew chat")
                ]
            ),
            LLMResult(tool_calls=[_tc("wait_for_replies", timeout_seconds=2)]),
            LLMResult(text="PRD drafted by the specialist. Ready to review."),
        ]
    )
    rig.llm.teammate.append(LLMResult(text="PRD: problem is the missing agentic surface."))
    await rig.runtime.on_user_message(space["id"], "write a PRD")
    await wait_run_done(rig.runtime, space["id"])

    names = [a["name"] for a in rig.store.list_agents(space["id"])]
    assert "PRD" in names
    prd = rig.store.get_agent_by_name(space["id"], "PRD")
    assert prd is not None
    assert "PRD agent" in (prd["role_prompt"] or "")

    msgs = rig.store.list_messages(space["id"])
    a2a = [m for m in msgs if m["role"] == "agent"]
    assert a2a, "expected visible agent-to-agent traffic"
    assert any("PRD: problem" in m["content"] for m in a2a)
    final = [m for m in msgs if m["role"] == "assistant"][-1]
    assert "PRD drafted" in final["content"]


async def test_desk_status_lands_in_transcript(rig, monkeypatch) -> None:
    monkeypatch.setenv("CREW_LIVE_PROBES", "0")
    space = rig.store.create_space("Night")
    rig.llm.manager.extend(
        [
            LLMResult(tool_calls=[_tc("desk_status")]),
            LLMResult(text="PRs checked in chat. Drop .eml for mail. No merge."),
        ]
    )
    await rig.runtime.on_user_message(space["id"], "check all my PRs")
    await wait_run_done(rig.runtime, space["id"])
    tools = [m for m in rig.store.list_messages(space["id"]) if m["role"] == "tool"]
    assert tools and "Cursor key" in tools[0]["content"]
    assert "auto-merge" in tools[0]["content"].lower() or "Do not auto-merge" in tools[0]["content"]
    answer = [m for m in rig.store.list_messages(space["id"]) if m["role"] == "assistant"][-1]
    assert "chat" in answer["content"].lower()



async def test_cortex_ask_envelope_is_on_the_tool_message(rig) -> None:
    space = rig.store.create_space("Data")
    rig.llm.manager.extend(
        [
            LLMResult(tool_calls=[_tc("cortex_ask", question="units on hand")]),
            LLMResult(text="42 units on hand (badge governed, audit-1)."),
        ]
    )
    await rig.runtime.on_user_message(space["id"], "how many units?")
    await wait_run_done(rig.runtime, space["id"])
    tools = [m for m in rig.store.list_messages(space["id"]) if m["role"] == "tool"]
    assert tools
    env = (tools[0]["meta"] or {}).get("envelope") or {}
    assert env["badge"] == "governed"
    assert env["audit_id"] == "audit-1"
    assert "42 units on hand" in tools[0]["content"]
    assert rig.bridge.asked == ["units on hand"]
    answer = [m for m in rig.store.list_messages(space["id"]) if m["role"] == "assistant"][-1]
    assert "42 units" in answer["content"]
    assert "governed" in answer["content"]


async def test_netie_board_is_in_the_transcript(rig, tmp_path, monkeypatch) -> None:
    claims = tmp_path / "CLAIMS.json"
    claims.write_text(
        json.dumps(
            {
                "tickets": [
                    {"ticket": "ANS-01", "role": "SEATED", "may_write": True, "owner_pr": "x#48"},
                    {"ticket": "FF-03", "role": "UNSEATED", "may_write": False, "owner_pr": "x#41"},
                ]
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("CREW_CLAIMS", str(claims))
    monkeypatch.setenv("CREW_RUNTIME", str(tmp_path / "RUNTIME.md"))
    space = rig.store.create_space("Board")
    rig.llm.manager.extend(
        [
            LLMResult(tool_calls=[_tc("netie_board")]),
            LLMResult(text="1 seated (ANS-01). I will not implement SEATED tickets or spawn a cloud swarm."),
        ]
    )
    await rig.runtime.on_user_message(space["id"], "what tickets are open?")
    await wait_run_done(rig.runtime, space["id"])
    tools = [m for m in rig.store.list_messages(space["id"]) if m["role"] == "tool"]
    assert tools
    assert "ANS-01" in tools[0]["content"]
    assert "seated=1" in tools[0]["content"]
    assert "cloud agent" in tools[0]["content"]
    answer = [m for m in rig.store.list_messages(space["id"]) if m["role"] == "assistant"][-1]
    assert "ANS-01" in answer["content"]
    assert "SEATED" in answer["content"]


async def test_dead_model_is_a_visible_system_message(rig) -> None:
    async def boom(*_a, **_k):
        raise LLMError("model backend unreachable")

    rig.runtime._llm = boom
    space = rig.store.create_space("HQ")
    await rig.runtime.on_user_message(space["id"], "hello?")
    await wait_run_done(rig.runtime, space["id"])
    sysmsgs = [m for m in rig.store.list_messages(space["id"]) if m["role"] == "system"]
    assert sysmsgs and "unreachable" in sysmsgs[0]["content"]


async def test_no_provider_tells_the_operator_what_to_set(
    monkeypatch: pytest.MonkeyPatch, rig
) -> None:
    for var in (
        "CREW_MODEL",
        "ANTHROPIC_API_KEY",
        "OPENROUTER_API_KEY",
        "DEEPSEEK_API_KEY",
        "OPENAI_API_KEY",
        "CREW_OPENAI_BASE_URL",
        "CURSOR_API_KEY",
        "XAI_API_KEY",
    ):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("CREW_ALLOW_OLLAMA", "0")
    space = rig.store.create_space("HQ")
    result = await rig.runtime.on_user_message(space["id"], "hello")
    assert "error" in result
    sysmsgs = [m for m in rig.store.list_messages(space["id"]) if m["role"] == "system"]
    assert sysmsgs and "OpenVault" in sysmsgs[0]["content"]


async def test_mcp_denied_when_master_switch_is_off(rig) -> None:
    fake = FakeMCPClient("uacc", [{"name": "click", "description": "click"}], armed=True)
    rig.mcp.clients["uacc"] = fake
    space = rig.store.create_space("Desk")
    rig.llm.manager.extend(
        [
            LLMResult(tool_calls=[_tc("mcp_uacc_click", x=10, y=10)]),
            LLMResult(text="Click was denied; computer control is off."),
        ]
    )
    await rig.runtime.on_user_message(space["id"], "click the button")
    await wait_run_done(rig.runtime, space["id"])
    assert fake.called == []
    tools = [m for m in rig.store.list_messages(space["id"]) if m["role"] == "tool"]
    assert tools and "denied" in tools[0]["content"].lower()
    answer = [m for m in rig.store.list_messages(space["id"]) if m["role"] == "assistant"][-1]
    assert "denied" in answer["content"].lower()


async def test_mutating_mcp_waits_for_operator_approve(settings, crew_env) -> None:
    settings.master_computer_control = True
    store = CrewStore(settings.db_path)
    bus = EventBus()
    mcp = MCPManager(settings.mcp_config_path, master_on=True)
    fake = FakeMCPClient("uacc", [{"name": "click", "inputSchema": {"type": "object"}}], armed=True)
    mcp.clients["uacc"] = fake
    llm = FakeLLM()
    runtime = CrewRuntime(store, bus, settings, mcp, FakeBridge(), llm_chat=llm)

    space = store.create_space("Desk")
    llm.manager.extend(
        [
            LLMResult(tool_calls=[_tc("mcp_uacc_click", x=1, y=2)]),
            LLMResult(text="Clicked after you approved."),
        ]
    )

    async def auto_approve() -> None:
        for _ in range(200):
            pending = store.pending_confirms(space["id"])
            if pending:
                runtime.decide_confirm(pending[0]["id"], True)
                return
            await asyncio.sleep(0.01)
        raise AssertionError("confirm never appeared")

    waiter = asyncio.create_task(auto_approve())
    await runtime.on_user_message(space["id"], "click it")
    await wait_run_done(runtime, space["id"])
    await waiter
    assert fake.called == [("click", {"x": 1, "y": 2})]
    answer = [m for m in store.list_messages(space["id"]) if m["role"] == "assistant"][-1]
    assert "approved" in answer["content"].lower()
    store.close()
