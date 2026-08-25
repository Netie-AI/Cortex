"""Agent-to-agent behaviour, asserted on the transcript the operator reads.

Each test here pins something the old string-queue inbox got wrong:
a reply that could not be matched to its question, a teammate that came back
from a restart with amnesia, and a wait that burned its whole timeout with
nobody working. Per CLAUDE.md section 8 the assertions are on stored messages
and agent status first; the model-side artifact is asserted only in addition,
where it is the only place the correlation is observable.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from CortexOS.crew import a2a
from CortexOS.crew.llm import LLMError, LLMResult, ToolCall
from tests.test_crew.conftest import wait_run_done

pytestmark = pytest.mark.asyncio


def _tc(tool: str, **args: object) -> ToolCall:
    return ToolCall(id=f"c-{tool}", name=tool, args=dict(args))


class NamedLLM:
    """Scripted completions keyed by agent name, not by charter.

    The shared teammate queue in conftest cannot express "Scout says X while
    Auditor says Y" - two concurrent teammates race for the same script. These
    tests need per-agent determinism, so route on the name the charter opens
    with. A scripted ``Exception`` is raised rather than returned, which is how
    a failing teammate is simulated, and a scripted number is a pause, which is
    how a teammate is held mid-flight while another agent talks to it.
    """

    def __init__(self) -> None:
        self.scripts: dict[str, list[Any]] = {}
        self.calls: list[dict[str, Any]] = []

    def script(self, who: str, *results: Any) -> None:
        self.scripts.setdefault(who, []).extend(results)

    def calls_for(self, who: str) -> list[dict[str, Any]]:
        return [c for c in self.calls if c["who"] == who]

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
        head = str(messages[0].get("content", "")) if messages else ""
        if head.startswith("You are the Manager"):
            who = "Manager"
        else:
            who = head.replace("You are ", "", 1).split(",")[0].strip()
        self.calls.append({"who": who, "messages": messages, "tools": tools})
        queue = self.scripts.get(who) or []
        while queue:
            item = queue.pop(0)
            if isinstance(item, (int, float)):
                await asyncio.sleep(float(item))
                continue
            if isinstance(item, Exception):
                raise item
            return item
        return LLMResult(text=f"({who} has no script)", model=model)


@pytest.fixture()
def rig2(rig, monkeypatch):
    """The standard rig with a name-routed model."""
    llm = NamedLLM()
    rig.runtime._llm = llm
    rig.llm = llm
    return rig


def _agent_msgs(rig, space_id: str) -> list[dict[str, Any]]:
    return [m for m in rig.store.list_messages(space_id) if m["role"] == "agent"]


def _tool_results(rig, who: str) -> list[str]:
    """Every tool result that was fed back to one agent."""
    out: list[str] = []
    for call in rig.llm.calls_for(who):
        for m in call["messages"]:
            if m.get("role") == "tool":
                out.append(str(m.get("content", "")))
    return out


# -- the switchboard on its own --------------------------------------------


async def test_answer_resolves_the_ask_and_is_not_also_queued() -> None:
    switch = a2a.Switchboard()
    future = switch.open_ask("mgr", "scout", "q1")
    answered = switch.deliver(
        a2a.Envelope(
            id="m2",
            kind=a2a.REPORT,
            from_id="scout",
            from_name="Scout",
            to_id="mgr",
            to_name="Manager",
            text="found it",
            seq=7,
        )
    )
    assert answered == a2a.REPORT
    kind, text = await asyncio.wait_for(future, 1)
    assert kind == a2a.REPORT
    assert text == "found it"
    # The asker was blocked on the future; queueing it too would be read twice.
    assert switch.mailbox("mgr").empty()


async def test_a_third_agents_report_does_not_answer_the_ask() -> None:
    switch = a2a.Switchboard()
    future = switch.open_ask("mgr", "scout", "q1")
    answered = switch.deliver(
        a2a.Envelope(
            id="m3",
            kind=a2a.REPORT,
            from_id="auditor",
            from_name="Auditor",
            to_id="mgr",
            to_name="Manager",
            text="unrelated noise",
            seq=8,
        )
    )
    assert answered is None
    assert not future.done()
    queued = switch.mailbox("mgr").drain()
    assert [e.text for e in queued] == ["unrelated noise"]
    future.cancel()


async def test_abandon_unblocks_an_ask_on_a_dead_target() -> None:
    switch = a2a.Switchboard()
    future = switch.open_ask("mgr", "ghost", "q1")
    freed = switch.abandon("ghost", "Ghost stopped running")
    assert freed == ["mgr"]
    kind, text = await asyncio.wait_for(future, 1)
    assert kind == a2a.NO_ANSWER
    assert "Ghost stopped running" in text
    assert switch.pending_count() == 0


async def test_drain_drops_envelopes_the_history_already_covers() -> None:
    box = a2a.Mailbox()
    for seq in (3, 4, 9):
        box.put(
            a2a.Envelope(
                id=f"m{seq}",
                kind=a2a.TELL,
                from_id="a",
                from_name="A",
                to_id="b",
                to_name="B",
                text=f"msg{seq}",
                seq=seq,
            )
        )
    kept = box.drain(after_seq=4)
    assert [e.text for e in kept] == ["msg9"]
    assert box.empty()


# -- the runtime, end to end ------------------------------------------------


async def test_ask_agent_returns_the_named_agents_answer_not_another(rig2) -> None:
    space = rig2.store.create_space("Correlate")
    rig2.llm.script(
        "Manager",
        LLMResult(tool_calls=[_tc("spawn_agent", name="Auditor", brief="audit the ledger")]),
        LLMResult(tool_calls=[_tc("spawn_agent", name="Scout", brief="scout the repo")]),
        LLMResult(tool_calls=[_tc("ask_agent", name="Scout", question="what did you find?")]),
        LLMResult(text="Scout found SCOUT-FINDING. Auditor reported separately."),
    )
    rig2.llm.script("Auditor", LLMResult(text="AUDITOR-NOISE"))
    rig2.llm.script("Scout", LLMResult(text="SCOUT-FINDING"))

    await rig2.runtime.on_user_message(space["id"], "ask scout")
    await wait_run_done(rig2.runtime, space["id"])

    # Visible: the question and both reports are in the transcript.
    agent_msgs = _agent_msgs(rig2, space["id"])
    asks = [m for m in agent_msgs if (m["meta"] or {}).get("a2a", {}).get("kind") == a2a.ASK]
    assert [m["content"] for m in asks] == ["what did you find?"]
    assert any("SCOUT-FINDING" in m["content"] for m in agent_msgs)
    assert any("AUDITOR-NOISE" in m["content"] for m in agent_msgs)

    # The correlation itself is only observable in what came back from the tool.
    answers = [t for t in _tool_results(rig2, "Manager") if "answered:" in t]
    assert answers, "ask_agent produced no answer"
    assert "Scout answered: SCOUT-FINDING" in answers[-1]
    assert "AUDITOR-NOISE" not in answers[-1]

    final = [m for m in rig2.store.list_messages(space["id"]) if m["role"] == "assistant"][-1]
    assert "SCOUT-FINDING" in final["content"]


async def test_ask_agent_reports_a_failed_target_instead_of_hanging(rig2) -> None:
    space = rig2.store.create_space("Ghost")
    rig2.llm.script(
        "Manager",
        LLMResult(tool_calls=[_tc("spawn_agent", name="Ghost", brief="go look")]),
        LLMResult(tool_calls=[_tc("ask_agent", name="Ghost", question="well?", timeout_seconds=30)]),
        LLMResult(text="Ghost failed; I am not inventing its answer."),
    )
    # Ghost is held mid-flight so the ask is delivered while it is still
    # working; it then dies without ever answering on its own.
    rig2.llm.script("Ghost", 0.2, LLMError("provider exploded"))

    await rig2.runtime.on_user_message(space["id"], "ask ghost")
    await wait_run_done(rig2.runtime, space["id"])

    ghost = rig2.store.get_agent_by_name(space["id"], "Ghost")
    assert ghost is not None and ghost["status"] == "failed"
    agent_msgs = _agent_msgs(rig2, space["id"])
    assert any("provider exploded" in m["content"] for m in agent_msgs)
    answers = [t for t in _tool_results(rig2, "Manager") if "answered:" in t]
    assert answers and "provider exploded" in answers[-1]


async def test_a_restarted_teammate_still_knows_its_own_history(rig2) -> None:
    space = rig2.store.create_space("Memory")
    rig2.llm.script(
        "Manager",
        LLMResult(tool_calls=[_tc("spawn_agent", name="Scout", brief="find the FIRST-THING")]),
        LLMResult(tool_calls=[_tc("wait_for_replies", timeout_seconds=5)]),
        LLMResult(tool_calls=[_tc("send_to_agent", name="Scout", message="now the SECOND-THING")]),
        LLMResult(tool_calls=[_tc("wait_for_replies", timeout_seconds=5)]),
        LLMResult(text="Scout handled both."),
    )
    rig2.llm.script(
        "Scout",
        LLMResult(text="FIRST-DONE"),
        LLMResult(text="SECOND-DONE"),
    )

    await rig2.runtime.on_user_message(space["id"], "two jobs for scout")
    await wait_run_done(rig2.runtime, space["id"])

    contents = [m["content"] for m in _agent_msgs(rig2, space["id"])]
    assert any("FIRST-DONE" in c for c in contents)
    assert any("SECOND-DONE" in c for c in contents), "Scout was never re-engaged"

    scout_calls = rig2.llm.calls_for("Scout")
    assert len(scout_calls) >= 2, "Scout was not restarted"
    replay = " ".join(str(m.get("content", "")) for m in scout_calls[-1]["messages"])
    # This is the amnesia fix: the restarted loop carries the original brief and
    # what Scout itself already answered, not just the newest line.
    assert "FIRST-THING" in replay, "restarted teammate lost its original brief"
    assert "FIRST-DONE" in replay, "restarted teammate lost its own earlier answer"
    assert "SECOND-THING" in replay


async def test_wait_for_replies_returns_at_once_when_nobody_is_working(rig2) -> None:
    space = rig2.store.create_space("Idle")
    rig2.llm.script(
        "Manager",
        LLMResult(tool_calls=[_tc("wait_for_replies", timeout_seconds=120)]),
        LLMResult(text="Nothing to wait for; answering directly."),
    )

    started = asyncio.get_running_loop().time()
    await rig2.runtime.on_user_message(space["id"], "hello")
    await wait_run_done(rig2.runtime, space["id"], timeout=10.0)
    elapsed = asyncio.get_running_loop().time() - started

    assert elapsed < 5, f"waited {elapsed:.1f}s for a wait that had nothing to wait for"
    results = _tool_results(rig2, "Manager")
    assert any("no teammate is still working" in t for t in results)


async def test_broadcast_reaches_every_other_agent(rig2) -> None:
    space = rig2.store.create_space("Standup")
    rig2.llm.script(
        "Manager",
        LLMResult(tool_calls=[_tc("spawn_agent", name="Alpha", brief="task a")]),
        LLMResult(tool_calls=[_tc("spawn_agent", name="Beta", brief="task b")]),
        LLMResult(tool_calls=[_tc("broadcast", message="STANDUP-NOW")]),
        LLMResult(text="Broadcast sent."),
    )
    rig2.llm.script("Alpha", LLMResult(text="alpha done"))
    rig2.llm.script("Beta", LLMResult(text="beta done"))

    await rig2.runtime.on_user_message(space["id"], "call a standup")
    await wait_run_done(rig2.runtime, space["id"])

    casts = [
        m
        for m in _agent_msgs(rig2, space["id"])
        if (m["meta"] or {}).get("a2a", {}).get("kind") == a2a.BROADCAST
    ]
    recipients = {(m["meta"] or {})["a2a"]["to"] for m in casts}
    assert recipients == {"Alpha", "Beta"}, f"broadcast went to {recipients}"
    assert all(m["content"] == "STANDUP-NOW" for m in casts)
    results = _tool_results(rig2, "Manager")
    assert any("broadcast to" in t for t in results)


async def test_send_to_agent_star_broadcasts(rig2) -> None:
    space = rig2.store.create_space("Star")
    rig2.llm.script(
        "Manager",
        LLMResult(tool_calls=[_tc("spawn_agent", name="Alpha", brief="task a")]),
        LLMResult(tool_calls=[_tc("send_to_agent", name="*", message="EVERYONE")]),
        LLMResult(text="done"),
    )
    rig2.llm.script("Alpha", LLMResult(text="alpha done"))

    await rig2.runtime.on_user_message(space["id"], "tell everyone")
    await wait_run_done(rig2.runtime, space["id"])

    casts = [
        m
        for m in _agent_msgs(rig2, space["id"])
        if (m["meta"] or {}).get("a2a", {}).get("kind") == a2a.BROADCAST
    ]
    assert [m["content"] for m in casts] == ["EVERYONE"]


async def test_every_a2a_message_records_who_and_what_kind(rig2) -> None:
    space = rig2.store.create_space("Wire")
    rig2.llm.script(
        "Manager",
        LLMResult(tool_calls=[_tc("spawn_agent", name="Scout", brief="the brief")]),
        LLMResult(tool_calls=[_tc("wait_for_replies", timeout_seconds=5)]),
        LLMResult(text="done"),
    )
    rig2.llm.script("Scout", LLMResult(text="the report"))

    await rig2.runtime.on_user_message(space["id"], "go")
    await wait_run_done(rig2.runtime, space["id"])

    wire = [((m["meta"] or {}).get("a2a") or {}) for m in _agent_msgs(rig2, space["id"])]
    assert {"from": "Manager", "to": "Scout", "kind": a2a.BRIEF, "reply_to": None} in wire
    assert {"from": "Scout", "to": "Manager", "kind": a2a.REPORT, "reply_to": None} in wire


async def test_an_agent_cannot_ask_itself(rig2) -> None:
    space = rig2.store.create_space("Self")
    rig2.llm.script(
        "Manager",
        LLMResult(tool_calls=[_tc("ask_agent", name="Manager", question="hello?")]),
        LLMResult(text="I cannot ask myself; answering directly."),
    )

    await rig2.runtime.on_user_message(space["id"], "ask yourself")
    await wait_run_done(rig2.runtime, space["id"], timeout=10.0)

    results = _tool_results(rig2, "Manager")
    assert any("cannot ask yourself" in t for t in results)


async def test_a_message_arriving_on_the_last_step_is_still_read(rig2) -> None:
    """A teammate that is already producing its answer must not drop the next
    message. Before the in-loop re-drain, anything delivered after the final
    step's drain sat in the mailbox with nobody left to read it."""
    space = rig2.store.create_space("Late")
    rig2.llm.script(
        "Manager",
        LLMResult(tool_calls=[_tc("spawn_agent", name="Scout", brief="ALPHA-BRIEF")]),
        # Pause so Scout starts and rebuilds its history *before* BETA is sent.
        0.05,
        LLMResult(tool_calls=[_tc("send_to_agent", name="Scout", message="BETA-LATE")]),
        LLMResult(tool_calls=[_tc("wait_for_replies", timeout_seconds=5)]),
        LLMResult(text="Scout covered both."),
    )
    rig2.llm.script("Scout", 0.2, LLMResult(text="scout-first"), LLMResult(text="scout-second"))

    await rig2.runtime.on_user_message(space["id"], "go")
    await wait_run_done(rig2.runtime, space["id"])

    scout_calls = rig2.llm.calls_for("Scout")
    seen = " ".join(
        str(m.get("content", "")) for call in scout_calls for m in call["messages"]
    )
    assert "BETA-LATE" in seen, "the late message was never delivered to Scout"

    reports = [
        m
        for m in _agent_msgs(rig2, space["id"])
        if (m["meta"] or {}).get("a2a", {}).get("kind") == a2a.REPORT
    ]
    assert [m["content"] for m in reports] == ["scout-second"]


async def test_spawn_then_message_does_not_start_the_teammate_twice(rig2) -> None:
    """A task created by spawn has not ticked yet, so the agent still reads as
    'idle' in the store. Waking on that status started a second copy of the
    same teammate, and the brief was worked twice."""
    space = rig2.store.create_space("Once")
    rig2.llm.script(
        "Manager",
        LLMResult(tool_calls=[_tc("spawn_agent", name="Scout", brief="ONLY-ONCE")]),
        LLMResult(tool_calls=[_tc("send_to_agent", name="Scout", message="and this too")]),
        LLMResult(tool_calls=[_tc("wait_for_replies", timeout_seconds=5)]),
        LLMResult(text="done"),
    )
    rig2.llm.script("Scout", 0.15, LLMResult(text="scout-report"))

    await rig2.runtime.on_user_message(space["id"], "go")
    await wait_run_done(rig2.runtime, space["id"])

    reports = [
        m
        for m in _agent_msgs(rig2, space["id"])
        if (m["meta"] or {}).get("a2a", {}).get("kind") == a2a.REPORT
    ]
    assert len(reports) == 1, f"Scout ran more than once: {[m['content'] for m in reports]}"
