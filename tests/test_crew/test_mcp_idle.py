"""Armed MCP servers must not sit resident between bursts of desktop work.

Computer control is used for a few seconds at a time. Holding a desktop
automation process alive between those bursts costs memory on a machine that
is already tight and buys no capability, so an armed server is suspended once
it goes quiet and started again by the next approved call.

Two things must stay true for that to be a saving rather than a regression:
a suspended server still advertises its tools (or nothing could wake it), and
a denied call never starts one (or "denied" would still cost a process).
"""

from __future__ import annotations

import asyncio

import pytest

from CortexOS.crew.events import EventBus
from CortexOS.crew.llm import LLMResult, ToolCall
from CortexOS.crew.mcp_client import SUSPENDED, MCPManager, idle_stop_seconds
from CortexOS.crew.runtime import CrewRuntime
from CortexOS.crew.store import CrewStore
from tests.test_crew.conftest import FakeBridge, FakeLLM, FakeMCPClient, wait_run_done

pytestmark = pytest.mark.asyncio


def _tc(tool: str, **args: object) -> ToolCall:
    return ToolCall(id=f"c-{tool}", name=tool, args=dict(args))


def _armed_rig(settings, *, tool: str = "screenshot"):
    """A crew wired to one armed fake MCP server, master switch on."""
    settings.master_computer_control = True
    store = CrewStore(settings.db_path)
    mcp = MCPManager(settings.mcp_config_path, master_on=True, idle_stop_s=60)
    fake = FakeMCPClient("uacc", [{"name": tool, "inputSchema": {"type": "object"}}], armed=True)
    mcp.clients["uacc"] = fake
    llm = FakeLLM()
    runtime = CrewRuntime(store, EventBus(), settings, mcp, FakeBridge(), llm_chat=llm)
    return store, mcp, fake, llm, runtime


# -- the reaper ------------------------------------------------------------


async def test_a_quiet_server_is_suspended(settings) -> None:
    _store, mcp, fake, _llm, _rt = _armed_rig(settings)
    fake.idle_s = 999.0
    napped = await mcp.reap_idle()
    assert napped == ["uacc"]
    assert fake.suspended is True
    assert fake.status == SUSPENDED
    _store.close()


async def test_a_recently_used_server_is_left_alone(settings) -> None:
    _store, mcp, fake, _llm, _rt = _armed_rig(settings)
    fake.idle_s = 1.0
    assert await mcp.reap_idle() == []
    assert fake.suspended is False
    _store.close()


async def test_suspension_can_be_turned_off(settings) -> None:
    settings.master_computer_control = True
    store = CrewStore(settings.db_path)
    mcp = MCPManager(settings.mcp_config_path, master_on=True, idle_stop_s=0)
    fake = FakeMCPClient("uacc", [{"name": "screenshot"}], armed=True)
    mcp.clients["uacc"] = fake
    fake.idle_s = 99_999.0
    assert await mcp.reap_idle() == []
    assert fake.suspended is False
    store.close()


async def test_idle_window_reads_the_environment(monkeypatch) -> None:
    monkeypatch.setenv("CREW_MCP_IDLE_STOP_S", "45")
    assert idle_stop_seconds() == 45
    monkeypatch.setenv("CREW_MCP_IDLE_STOP_S", "not-a-number")
    assert idle_stop_seconds() == 300  # falls back rather than crashing the server
    monkeypatch.setenv("CREW_MCP_IDLE_STOP_S", "0")
    assert idle_stop_seconds() == 0


# -- what a suspended server still looks like -------------------------------


async def test_a_suspended_server_still_advertises_its_tools(settings) -> None:
    _store, mcp, fake, _llm, _rt = _armed_rig(settings, tool="screenshot")
    fake.idle_s = 999.0
    await mcp.reap_idle()
    catalog = [name for _server, tool in mcp.tool_catalog() for name in [tool["name"]]]
    assert catalog == ["screenshot"], "a sleeping server that hides its tools can never wake"
    _store.close()


async def test_status_says_suspended_rather_than_running(settings) -> None:
    _store, mcp, fake, _llm, _rt = _armed_rig(settings)
    fake.idle_s = 999.0
    await mcp.reap_idle()
    row = next(r for r in mcp.status() if r["name"] == "uacc")
    # Still armed, but honestly reported as not running (KB R-0011).
    assert row["armed"] is True
    assert row["suspended"] is True
    assert row["running"] is False
    assert row["idle_stop_s"] == 60
    _store.close()


# -- waking, and not waking -------------------------------------------------


async def test_an_approved_call_wakes_a_suspended_server(settings) -> None:
    store, mcp, fake, llm, runtime = _armed_rig(settings, tool="screenshot")
    fake.idle_s = 999.0
    await mcp.reap_idle()
    assert fake.suspended is True
    starts_before = fake.starts

    space = store.create_space("Desk")
    llm.manager.extend(
        [
            LLMResult(tool_calls=[_tc("mcp_uacc_screenshot")]),
            LLMResult(text="Screenshot taken after waking the server."),
        ]
    )
    await runtime.on_user_message(space["id"], "take a screenshot")
    await wait_run_done(runtime, space["id"])

    assert fake.starts == starts_before + 1, "suspended server was never restarted"
    assert fake.called == [("screenshot", {})]
    tools = [m for m in store.list_messages(space["id"]) if m["role"] == "tool"]
    assert tools and "ok-screenshot" in tools[0]["content"]
    answer = [m for m in store.list_messages(space["id"]) if m["role"] == "assistant"][-1]
    assert "screenshot" in answer["content"].lower()
    store.close()


async def test_a_denied_call_does_not_start_a_suspended_server(settings, crew_env) -> None:
    """Master switch off: the call is refused, and no process is spawned for it."""
    settings.master_computer_control = False
    store = CrewStore(settings.db_path)
    mcp = MCPManager(settings.mcp_config_path, master_on=False, idle_stop_s=60)
    fake = FakeMCPClient("uacc", [{"name": "click", "inputSchema": {"type": "object"}}], armed=True)
    fake.suspended = True
    fake.status = SUSPENDED
    fake.tools = []
    mcp.clients["uacc"] = fake
    llm = FakeLLM()
    runtime = CrewRuntime(store, EventBus(), settings, mcp, FakeBridge(), llm_chat=llm)

    space = store.create_space("Desk")
    llm.manager.extend(
        [
            LLMResult(tool_calls=[_tc("mcp_uacc_click", x=1, y=2)]),
            LLMResult(text="Click was denied; computer control is off."),
        ]
    )
    await runtime.on_user_message(space["id"], "click it")
    await wait_run_done(runtime, space["id"])

    assert fake.starts == 0, "a denied call started a desktop automation process"
    assert fake.called == []
    tools = [m for m in store.list_messages(space["id"]) if m["role"] == "tool"]
    assert tools and "denied" in tools[0]["content"].lower()
    store.close()


async def test_the_reaper_starts_and_stops_cleanly(settings) -> None:
    _store, mcp, fake, _llm, _rt = _armed_rig(settings)
    mcp.idle_stop_s = 1
    mcp.start_reaper(interval_s=1)
    assert mcp._reaper is not None
    fake.idle_s = 999.0
    await asyncio.sleep(1.3)
    assert fake.suspended is True, "the background reaper never ran"
    await mcp.stop_reaper()
    assert mcp._reaper is None
    _store.close()


async def test_the_reaper_does_not_start_when_suspension_is_off(settings) -> None:
    _store, mcp, _fake, _llm, _rt = _armed_rig(settings)
    mcp.idle_stop_s = 0
    mcp.start_reaper()
    assert mcp._reaper is None
    _store.close()
