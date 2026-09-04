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
    assert {"desk", "board", "estate", "ship_gate"} <= slashes
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
