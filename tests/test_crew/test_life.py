"""Spawn/kill/stop, smart-idle, and goal/active persistence.

Asserted on stored statuses, transcript refusals, and HUD routes the operator
reads (CLAUDE.md section 8). No second daemon loop: parked teammates wait on
the existing A2A mailbox inside the same run task.
"""

from __future__ import annotations

import asyncio

import pytest

from CortexOS.crew import a2a, life
from CortexOS.crew.llm import LLMResult
from tests.test_crew.conftest import wait_run_done
from tests.test_crew.test_a2a import NamedLLM, _agent_msgs, _tc, _tool_results

pytestmark = pytest.mark.asyncio


@pytest.fixture()
def rig2(rig):
    llm = NamedLLM()
    rig.runtime._llm = llm
    rig.llm = llm
    return rig


async def test_operator_spawn_kill_stop_statuses(rig2) -> None:
    space = rig2.store.create_space("Life")
    spawned = await rig2.runtime.operator_spawn(
        space["id"], {"name": "Scout", "brief": "watch", "mode": "active"}
    )
    assert spawned.get("ok") is True
    scout = spawned["agent"]
    assert scout["status"] in {life.STATUS_IDLE, life.STATUS_ACTIVE}
    assert scout["mode"] == life.MODE_ACTIVE

    stopped = rig2.runtime.stop_agent(scout["id"])
    assert stopped is not None and not stopped.get("error")
    parked = rig2.store.get_agent(scout["id"])
    assert parked is not None and parked["status"] == life.STATUS_IDLE
    assert life.can_accept(parked)

    killed = rig2.runtime.kill_agent(scout["id"], reason="operator killed")
    assert killed is not None
    assert killed["status"] == life.STATUS_STOPPED
    assert int(killed["alive"]) == 0
    assert "operator killed" in killed["stop_reason"]
    manager_kill = rig2.runtime.kill_agent(rig2.runtime.ensure_manager(space["id"])["id"])
    assert manager_kill is not None and "DENIED" in str(manager_kill.get("error"))


async def test_killed_agent_ask_is_a_visible_refusal(rig2) -> None:
    space = rig2.store.create_space("Ghost")
    rig2.llm.script(
        "Manager",
        LLMResult(tool_calls=[_tc("spawn_agent", name="Ghost", brief="go look")]),
        LLMResult(tool_calls=[_tc("kill_agent", name="Ghost", reason="silent-dead")]),
        LLMResult(tool_calls=[_tc("ask_agent", name="Ghost", question="well?")]),
        LLMResult(text="Ghost is dead; I am not inventing its answer."),
    )
    rig2.llm.script("Ghost", 0.2, LLMResult(text="should-not-land"))

    await rig2.runtime.on_user_message(space["id"], "kill ghost")
    await wait_run_done(rig2.runtime, space["id"])

    ghost = rig2.store.get_agent_by_name(space["id"], "Ghost")
    assert ghost is not None
    assert ghost["status"] == life.STATUS_STOPPED
    sysmsgs = [m for m in rig2.store.list_messages(space["id"]) if m["role"] == "system"]
    assert any("DENIED" in m["content"] and "silent-dead" in m["content"] for m in sysmsgs)
    results = _tool_results(rig2, "Manager")
    assert any("DENIED" in t and "silent-dead" in t for t in results)
    final = [m for m in rig2.store.list_messages(space["id"]) if m["role"] == "assistant"][-1]
    assert "dead" in final["content"].lower() or "not inventing" in final["content"].lower()


async def test_smart_idle_accepts_a_second_task_without_a_daemon(rig2) -> None:
    space = rig2.store.create_space("Idle")
    rig2.llm.script(
        "Manager",
        LLMResult(tool_calls=[_tc("spawn_agent", name="Scout", brief="FIRST-THING")]),
        LLMResult(tool_calls=[_tc("wait_for_replies", timeout_seconds=5)]),
        LLMResult(tool_calls=[_tc("send_to_agent", name="Scout", message="SECOND-THING")]),
        LLMResult(tool_calls=[_tc("wait_for_replies", timeout_seconds=5)]),
        LLMResult(text="Scout handled both from idle."),
    )
    rig2.llm.script("Scout", LLMResult(text="FIRST-DONE"), LLMResult(text="SECOND-DONE"))

    await rig2.runtime.on_user_message(space["id"], "two jobs")
    await wait_run_done(rig2.runtime, space["id"])

    contents = [m["content"] for m in _agent_msgs(rig2, space["id"])]
    assert any("FIRST-DONE" in c for c in contents)
    assert any("SECOND-DONE" in c for c in contents)
    scout = rig2.store.get_agent_by_name(space["id"], "Scout")
    assert scout is not None
    assert scout["status"] in {life.STATUS_IDLE, life.STATUS_GOAL}
    handle = rig2.runtime._handles.get(scout["id"])
    for _ in range(50):
        if handle is None or handle.task is None or handle.task.done():
            break
        await asyncio.sleep(0.01)
    handle = rig2.runtime._handles.get(scout["id"])
    assert handle is None or handle.task is None or handle.task.done()


async def test_goal_mode_survives_chat_clear(rig2) -> None:
    space = rig2.store.create_space("Goal")
    spawned = await rig2.runtime.operator_spawn(
        space["id"],
        {"name": "Keeper", "brief": "hold the line", "mode": "goal", "goal": "hold the line"},
    )
    keeper = spawned["agent"]
    rig2.store.add_message(space["id"], "user", "forget this")
    cleared = rig2.runtime.clear_chat(space["id"])
    assert cleared["ok"] is True
    users = [m for m in rig2.store.list_messages(space["id"]) if m["role"] == "user"]
    assert users == []
    again = rig2.store.get_agent(keeper["id"])
    assert again is not None
    assert again["mode"] == life.MODE_GOAL
    assert again["goal_text"] == "hold the line"
    assert again["status"] == life.STATUS_GOAL

    worker = await rig2.runtime.operator_spawn(
        space["id"], {"name": "Temp", "brief": "one shot", "mode": "active"}
    )
    temp = worker["agent"]
    rig2.runtime.clear_chat(space["id"])
    temp2 = rig2.store.get_agent(temp["id"])
    assert temp2 is not None
    assert temp2["mode"] == life.MODE_ACTIVE
    assert temp2["status"] == life.STATUS_IDLE


async def test_mailbox_unget_keeps_smart_idle_accept_order() -> None:
    box = a2a.Mailbox()
    first = a2a.Envelope(
        id="m1", kind=a2a.TELL, from_id="a", from_name="A",
        to_id="b", to_name="B", text="one", seq=1,
    )
    second = a2a.Envelope(
        id="m2", kind=a2a.TELL, from_id="a", from_name="A",
        to_id="b", to_name="B", text="two", seq=2,
    )
    box.put(first)
    box.put(second)
    taken = await box.get(timeout=0.1)
    assert taken is not None and taken.text == "one"
    box.unget(taken)
    drained = box.drain()
    assert [e.text for e in drained] == ["one", "two"]
