"""Slash catalog, @ resolution, and Switchboard delivery. Operator-visible."""

from __future__ import annotations

import pytest

from CortexOS.crew.commands import (
    catalog,
    filter_commands,
    filter_mentions,
    match_command,
    mention_targets,
    parse,
    parse_mentions,
    resolve_mentions,
)
from CortexOS.crew.llm import LLMResult
from tests.test_crew.conftest import wait_run_done


def test_catalog_lists_desk_routines_and_skills(settings) -> None:
    rows = catalog(settings.data_dir / "skills")
    slashes = {r["slash"] for r in rows}
    assert {
        "desk",
        "board",
        "estate",
        "ship_gate",
        "spawn",
        "kill",
        "stop",
        "idle",
        "wait",
        "goal",
        "done",
        "remember",
        "memory",
        "recall",
        "forget",
    } <= slashes
    assert "pr-check" in slashes
    assert "build" in slashes
    desk = match_command("desk_status", settings.data_dir / "skills")
    assert desk is not None and desk["action"] == "desk_status"
    assert filter_commands(rows, "ship")[0]["slash"] in {"ship_gate", "ship"}


def test_parse_slash_and_mentions() -> None:
    parsed = parse("/desk")
    assert parsed.command is not None
    assert parsed.command["action"] == "desk_status"
    assert parsed.rest == ""
    hit = parse("/ship_gate Cortex")
    assert hit.command is not None and hit.command["action"] == "ship_gate"
    assert hit.rest == "Cortex"
    spawn = parse("/spawn Scout | watch")
    assert spawn.command is not None and spawn.command["action"] == "spawn"
    assert spawn.command["kind"] == "life"
    assert spawn.rest == "Scout | watch"
    names = parse_mentions("@PRD check the brief with @Gate")
    assert names == ("PRD", "Gate")
    resolved = resolve_mentions(names)
    kinds = {m.name: m.kind for m in resolved}
    assert kinds["PRD"] == "role"
    assert kinds["Gate"] == "role"
    mixed = parse("@Scout ping")
    assert mixed.command is None
    assert [m.name for m in mixed.mentions] == ["Scout"]
    assert mixed.mentions[0].kind == "unknown"


def test_mention_targets_prefer_live_teammates() -> None:
    agents = [{"id": "a1", "name": "Scout", "capability": "PRD", "status": "idle"}]
    rows = mention_targets(agents)
    names = [r["name"] for r in rows]
    assert names[0] == "Manager"
    assert "Scout" in names
    assert "PRD" in names
    scout = next(r for r in rows if r["name"] == "Scout")
    assert scout["kind"] == "teammate" and scout["id"] == "a1"
    hits = filter_mentions(rows, "pr")
    assert any(h["name"] == "PRD" for h in hits)


@pytest.mark.asyncio
async def test_slash_desk_writes_tool_without_a_run(rig) -> None:
    space = rig.store.create_space("HQ")
    posted = await rig.runtime.on_user_message(space["id"], "/desk")
    assert posted.get("run_id") is None
    assert posted.get("command") == "desk"
    msgs = rig.store.list_messages(space["id"])
    assert [m["role"] for m in msgs] == ["user", "tool"]
    user, tool = msgs
    assert user["content"] == "/desk"
    assert user["meta"]["a2a"]["from"] == "operator"
    assert user["meta"]["a2a"]["to"] == "Manager"
    assert tool["meta"]["tool"] == "desk_status"
    assert tool["meta"]["slash"] is True
    assert "Human is money" in tool["content"] or "Cursor key" in tool["content"]
    assert not rig.runtime._space_run.get(space["id"])


@pytest.mark.asyncio
async def test_slash_remember_writes_facts_md_without_a_run(rig) -> None:
    space = rig.store.create_space("HQ")
    posted = await rig.runtime.on_user_message(
        space["id"], "/remember crew-port | which port crew listens on | 8020"
    )
    assert posted.get("run_id") is None
    assert posted.get("command") == "remember"
    tool = [m for m in rig.store.list_messages(space["id"]) if m["role"] == "tool"][-1]
    assert "remembered 'crew-port'" in tool["content"]
    facts = rig.settings.data_dir / "spaces" / space["id"] / "memory" / "facts.md"
    assert "8020" in facts.read_text(encoding="utf-8")
    rig.runtime.clear_chat(space["id"])
    assert "8020" in facts.read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_slash_spawn_kill_idle_wait_goal_without_a_run(rig) -> None:
    from CortexOS.crew import life

    space = rig.store.create_space("HQ")
    posted = await rig.runtime.on_user_message(
        space["id"], "/spawn Scout | watch the belt | hold the line"
    )
    assert posted.get("run_id") is None
    assert posted.get("command") == "spawn"
    scout = rig.store.get_agent_by_name(space["id"], "Scout")
    assert scout is not None
    assert scout["mode"] == life.MODE_GOAL
    assert scout["goal_text"] == "hold the line"
    parked = await rig.runtime.on_user_message(space["id"], "/idle Scout")
    assert parked.get("run_id") is None
    idle = rig.store.get_agent(scout["id"])
    assert idle is not None and idle["status"] in {life.STATUS_IDLE, life.STATUS_GOAL}
    waiting = await rig.runtime.on_user_message(space["id"], "/wait Scout")
    assert waiting.get("run_id") is None
    wait_row = rig.store.get_agent(scout["id"])
    assert wait_row is not None and wait_row["status"] == life.STATUS_WAITING
    goaled = await rig.runtime.on_user_message(space["id"], "/goal Scout | keep watching")
    assert goaled.get("run_id") is None
    again = rig.store.get_agent(scout["id"])
    assert again is not None and again["mode"] == life.MODE_GOAL
    assert again["goal_text"] == "keep watching"
    killed = await rig.runtime.on_user_message(space["id"], "/kill Scout | test done")
    assert killed.get("run_id") is None
    dead = rig.store.get_agent(scout["id"])
    assert dead is not None and dead["status"] == life.STATUS_STOPPED
    await rig.runtime.on_user_message(space["id"], "/kill Nobody")
    tool = [m for m in rig.store.list_messages(space["id"]) if m["role"] == "tool"][-1]
    assert "DENIED" in tool["content"] and "Nobody" in tool["content"]
    assert not rig.runtime._space_run.get(space["id"])


@pytest.mark.asyncio
async def test_slash_done_refuses_seated_and_hitl_closes_unseated(
    rig, tmp_path, monkeypatch
) -> None:
    from CortexOS.crew import github as github_mod

    claims = tmp_path / "CLAIMS.json"
    claims.write_text(
        '{"tickets":[{"ticket":"Netie-AI/Cortex#128","owner_pr":"Netie-AI/Cortex#128","role":"SEATED"}]}',
        encoding="utf-8",
    )
    monkeypatch.setenv("CREW_CLAIMS", str(claims))
    space = rig.store.create_space("HQ")
    seated = await rig.runtime.on_user_message(space["id"], "/done Netie-AI/Cortex#128")
    assert seated.get("run_id") is None
    tool = [m for m in rig.store.list_messages(space["id"]) if m["role"] == "tool"][-1]
    assert "SEATED" in tool["content"]
    assert rig.store.pending_confirms(space["id"]) == []

    claims.write_text('{"tickets":[]}', encoding="utf-8")
    posted = await rig.runtime.on_user_message(
        space["id"], "/done Netie-AI/Cortex#99 | verified on desk"
    )
    assert posted.get("run_id") is None
    pending = rig.store.pending_confirms(space["id"])
    assert len(pending) == 1
    assert pending[0]["tool"] == "close_issue"
    assert pending[0]["args"]["spec"] == "Netie-AI/Cortex#99"

    closed: dict[str, str] = {}

    def fake_close(spec, *, comment="", runner=None):  # noqa: ANN001, ARG001
        closed["spec"] = spec
        closed["comment"] = comment
        return {
            "ok": True,
            "spec": spec,
            "detail": "Closed",
            "law": "Closed the issue. Did not merge a PR. Ticket Runner seats writers.",
        }

    monkeypatch.setattr(github_mod, "close_issue", fake_close)
    row = rig.runtime.decide_confirm(pending[0]["id"], True)
    assert row is not None and row["status"] == "approved"
    assert closed["spec"] == "Netie-AI/Cortex#99"
    assert "verified" in closed["comment"]
    last = [m for m in rig.store.list_messages(space["id"]) if m["role"] == "tool"][-1]
    assert "Closed" in last["content"]
    denied = await rig.runtime.on_user_message(space["id"], "/done not-a-ticket")
    assert denied.get("run_id") is None
    bad = [m for m in rig.store.list_messages(space["id"]) if m["role"] == "tool"][-1]
    assert "DENIED" in bad["content"]
    assert not rig.runtime._space_run.get(space["id"])


@pytest.mark.asyncio
async def test_at_teammate_routes_through_switchboard(rig) -> None:
    space = rig.store.create_space("HQ")
    manager = rig.runtime.ensure_manager(space["id"])
    scout = rig.store.upsert_agent(space["id"], "Scout", role_prompt="Scout the brief.")
    rig.llm.manager.append(LLMResult(text="Noted."))
    rig.llm.teammate.append(LLMResult(text="Scout has the ping."))
    posted = await rig.runtime.on_user_message(space["id"], "@Scout ping the board")
    assert posted.get("run_id")
    user = rig.store.list_messages(space["id"])[0]
    assert user["role"] == "user"
    assert user["to_agent_id"] == scout["id"]
    assert user["meta"]["a2a"]["from"] == "operator"
    assert user["meta"]["a2a"]["to"] == "Scout"
    assert user["meta"]["mentions"][0]["kind"] == "teammate"
    waiting = rig.runtime.switch.mailbox(scout["id"]).snapshot()
    # New run rebuilds teammate history from the transcript; the envelope may
    # already have been drained. Either the mailbox or history must show it.
    history = " ".join(
        str(m.get("content") or "")
        for m in rig.runtime._teammate_history(space["id"], scout, None)
    )
    assert waiting or "[operator to Scout]" in history
    if waiting:
        assert waiting[0].kind == "user"
        assert waiting[0].from_name == "operator"
        assert waiting[0].to_name == "Scout"
        assert waiting[0].to_id == scout["id"]
    await wait_run_done(rig.runtime, space["id"])
    # Manager still ran; transcript keeps sender/recipient on the user line.
    assert user["meta"]["a2a"]["from"] == "operator"
    assert manager["name"] == "Manager"


@pytest.mark.asyncio
async def test_at_role_stamps_recipient_without_a_teammate(rig) -> None:
    space = rig.store.create_space("HQ")
    rig.llm.manager.append(LLMResult(text="I will spawn PRD."))
    await rig.runtime.on_user_message(space["id"], "@PRD write the spec")
    await wait_run_done(rig.runtime, space["id"])
    user = [m for m in rig.store.list_messages(space["id"]) if m["role"] == "user"][0]
    assert user["meta"]["a2a"]["from"] == "operator"
    assert user["meta"]["a2a"]["to"] == "PRD"
    assert user["meta"]["mentions"][0]["kind"] == "role"
    manager = rig.store.get_agent_by_name(space["id"], "Manager")
    assert manager is not None
    assert user["to_agent_id"] == manager["id"]
    env_text = " ".join(
        str(m.get("content") or "")
        for m in rig.runtime._manager_history(space["id"])
    )
    assert "[operator -> PRD]" in env_text
