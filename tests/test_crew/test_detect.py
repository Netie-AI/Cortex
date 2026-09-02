"""Detect + spawn use cases. Assert the transcript the operator sees."""

from __future__ import annotations

import pytest

from CortexOS.crew.detect import (
    attached_skills,
    match_capabilities,
    plan,
    playbooks,
    render,
    slash_skill,
    wave_plan,
)
from CortexOS.crew.llm import LLMResult, ToolCall
from CortexOS.crew.runtime import CrewRuntime
from CortexOS.crew.store import CrewStore
from tests.test_crew.conftest import FakeBridge, FakeLLM, FakeMCPClient, wait_run_done


def _tc(tool: str, **args: object) -> ToolCall:
    return ToolCall(id=f"c-{tool}", name=tool, args=dict(args))


# Open-source shaped prompts: pattern copied, product labels not cargo-culted.
DETECT_FIXTURES = (
    (
        "Reply with exactly the word pong and do not spawn agents.",
        "single_agent",
        (),
        False,
    ),
    (
        "write a PRD for crew chat",
        "orchestrator_subagent",
        ("PRD",),
        True,
    ),
    (
        "write the PRD then tickets then gate this against invariants",
        "generator_verifier",
        ("Ticket", "PRD", "Gate"),
        True,
    ),
    (
        "Task: explore the login bug then write a PRD in parallel",
        "orchestrator_subagent",
        ("Ticket", "PRD"),
        True,
    ),
    (
        "Spawn a subagent with find-skills to write the PRD",
        "orchestrator_subagent",
        ("PRD", "Skills"),
        True,
    ),
    (
        "You are a helpful assistant. Just say hi.",
        "single_agent",
        (),
        False,
    ),
    (
        "File a GitHub issue: login 500 on staging",
        "orchestrator_subagent",
        ("Ticket",),
        True,
    ),
)


@pytest.mark.parametrize("text,pattern,caps,spawn", DETECT_FIXTURES)
def test_detect_fixtures_open_source_shapes(text, pattern, caps, spawn) -> None:
    got = plan(text)
    assert got.pattern == pattern
    assert got.spawn is spawn
    for name in caps:
        assert name in got.capabilities
    assert match_capabilities(text) == got.capabilities or set(caps) <= set(got.capabilities)


def test_empty_plan_is_idle_not_single_agent() -> None:
    got = plan("")
    assert got.pattern == "idle"
    assert got.spawn is False
    assert got.capabilities == ()
    assert plan("   ").pattern == "idle"
    blob = got.as_dict()
    assert blob["pattern"] == "idle"
    assert blob["spawn"] is False
    assert blob["capabilities"] == []


def test_linkedin_and_seo_detect() -> None:
    li = plan("help me go to linkedin connect a few people politely")
    assert "Marketing" in li.capabilities
    assert "computer-reach" in attached_skills(li.capabilities)
    seo = plan("seo meta description for the hours page")
    assert "SEO" in seo.capabilities
    assert "seo" in attached_skills(seo.capabilities)
    got = plan("draft outreach for a factory sales inbox")
    assert "Marketing" in got.capabilities
    assert got.spawn is True
    skills = attached_skills(got.capabilities)
    assert "outreach" in skills
    assert "chat-human" in skills
    assert "computer-reach" in skills
    text = render(got)
    assert "Default skills (auto-copied on spawn):" in text
    assert "outreach" in text
    assert "Matching playbooks are inlined below" in text
    payload = got.as_dict()
    assert payload["load_skill_now"] is True
    assert "outreach" in payload["skills"]


def test_playbooks_inline_the_matching_body(tmp_path) -> None:
    from CortexOS.crew.board import ensure_skill_packs

    folder = tmp_path / "skills"
    ensure_skill_packs(folder)
    text = playbooks(("outreach",), folder)
    assert "[playbook: outreach]" in text
    assert "Jian Hong" in text
    missing = playbooks(("no-such-skill",), folder)
    assert "Not inlined; load_skill: no-such-skill" in missing


def test_slash_skill_is_a_leading_name_not_a_path() -> None:
    assert slash_skill("/outreach draft a factory note") == "outreach"
    assert slash_skill("/tone") == "tone"
    assert slash_skill("/crew/health") is None
    assert slash_skill("//outreach") is None
    assert slash_skill("please /outreach later") is None


def test_wave_plan_inlines_independent_caps_together() -> None:
    assert wave_plan(("PRD",)) == ""
    text = wave_plan(("PRD", "Ticket", "Gate"))
    assert "plan:" in text
    assert "nothing was spawned" in text
    assert "wave 0" in text
    assert "PRD" in text and "Ticket" in text and "Gate" in text


@pytest.mark.asyncio
async def test_slash_invoke_inlines_that_playbook(rig) -> None:
    space = rig.store.create_space("Slash")
    rig.llm.manager.append(LLMResult(text="Following the tone playbook: ASCII only."))
    await rig.runtime.on_user_message(space["id"], "/tone say hi")
    await wait_run_done(rig.runtime, space["id"])
    roster = str(rig.llm.calls[0]["messages"][1]["content"])
    assert "Operator invoked /tone" in roster
    assert "[playbook: tone]" in roster
    assert "Grok-bot" in roster or "ASCII" in roster


def test_feedback_and_auth_detect() -> None:
    got = plan("learn from this bad feedback in the Gmail thread")
    assert "Skills" in got.capabilities
    assert "feedback-learn" in attached_skills(got.capabilities)
    auth = plan("who authorised the website modifications")
    assert "Email" in auth.capabilities
    assert "feedback-learn" in attached_skills(auth.capabilities)


def test_add_named_skill_detects_ingest_without_the_word_design() -> None:
    got = plan("Add impeccable skill into skill storage")
    assert "Skills" in got.capabilities
    assert got.spawn is True
    skills = attached_skills(got.capabilities)
    assert "skill-ingest" in skills
    text = render(got)
    assert "skill-ingest" in text


def test_make_a_website_detects_surface_analog_without_saying_design() -> None:
    got = plan("make a website like https://www.igloo.inc/")
    assert "Surface" in got.capabilities
    assert "analog-surface" in attached_skills(got.capabilities)
    clone = plan("clone https://www.igloo.inc/")
    assert "Surface" in clone.capabilities


def test_3d_landing_promotes_surface_verify_not_boxes() -> None:
    got = plan(
        "generate a 3d asset website landing page to promote cortex-crew. "
        "verify it is not boxes or pasted images."
    )
    assert "Surface" in got.capabilities
    assert got.verify is True
    assert got.pattern == "generator_verifier"
    assert "analog-surface" in attached_skills(got.capabilities)


@pytest.mark.asyncio
async def test_pong_does_not_spawn_teammate(rig) -> None:
    space = rig.store.create_space("HQ")
    rig.llm.manager.append(LLMResult(text="pong"))
    await rig.runtime.on_user_message(
        space["id"], "Reply with exactly the word pong and do not spawn agents."
    )
    await wait_run_done(rig.runtime, space["id"])
    names = [a["name"] for a in rig.store.list_agents(space["id"])]
    assert names == ["Manager"]
    msgs = rig.store.list_messages(space["id"])
    assert msgs[-1]["content"] == "pong"
    roster = rig.llm.calls[0]["messages"][1]["content"]
    assert "Do not call spawn_agent" in roster
    assert "Spawn the " not in roster


@pytest.mark.asyncio
async def test_multi_capability_turn_inlines_a_wave_plan(rig) -> None:
    space = rig.store.create_space("Waves")
    rig.llm.manager.append(LLMResult(text="PRD then tickets then gate, in that wave."))
    await rig.runtime.on_user_message(
        space["id"], "write the PRD then tickets then gate this against invariants"
    )
    await wait_run_done(rig.runtime, space["id"])
    roster = str(rig.llm.calls[0]["messages"][1]["content"])
    assert "[waves]" in roster
    assert "wave 0" in roster
    assert "nothing was spawned" in roster


@pytest.mark.asyncio
async def test_custom_name_keeps_job_name_and_copies_prd(rig) -> None:
    space = rig.store.create_space("Build")
    rig.llm.manager.extend(
        [
            LLMResult(
                tool_calls=[
                    _tc(
                        "spawn_agent",
                        name="sku-filter-prd",
                        capability="PRD",
                        brief="write the PRD",
                    )
                ]
            ),
            LLMResult(tool_calls=[_tc("wait_for_replies", timeout_seconds=2)]),
            LLMResult(text="sku-filter-prd drafted the PRD."),
        ]
    )
    rig.llm.teammate.append(LLMResult(text="PRD: problem is SKU filter encoding."))
    await rig.runtime.on_user_message(space["id"], "write a PRD")
    await wait_run_done(rig.runtime, space["id"])
    names = [a["name"] for a in rig.store.list_agents(space["id"])]
    assert "sku-filter-prd" in names
    assert "PRD" not in names or rig.store.get_agent_by_name(space["id"], "sku-filter-prd")
    prd = rig.store.get_agent_by_name(space["id"], "sku-filter-prd")
    assert prd is not None
    assert prd["name"] == "sku-filter-prd"
    assert "PRD agent" in (prd["role_prompt"] or "")
    assert prd["capability"] == "PRD"
    final = [m for m in rig.store.list_messages(space["id"]) if m["role"] == "assistant"][-1]
    assert "sku-filter-prd" in final["content"]


@pytest.mark.asyncio
async def test_marketing_spawn_copies_outreach_skill(rig) -> None:
    space = rig.store.create_space("Mail")
    rig.llm.manager.extend(
        [
            LLMResult(
                tool_calls=[
                    _tc(
                        "spawn_agent",
                        name="factory-mail",
                        capability="Marketing",
                        brief="draft one extract",
                    )
                ]
            ),
            LLMResult(tool_calls=[_tc("wait_for_replies", timeout_seconds=2)]),
            LLMResult(text="factory-mail drafted the extract."),
        ]
    )
    rig.llm.teammate.append(LLMResult(text="Draft ready. Human sends."))
    await rig.runtime.on_user_message(space["id"], "draft outreach for a factory")
    await wait_run_done(rig.runtime, space["id"])
    agent = rig.store.get_agent_by_name(space["id"], "factory-mail")
    assert agent is not None
    prompt = agent["role_prompt"] or ""
    assert "Skill outreach" in prompt
    assert "Skill chat-human" in prompt
    assert "Skill computer-reach" in prompt
    assert "Jian Hong" in prompt
    stored = str(agent.get("skills") or "")
    assert "outreach" in stored
    assert "chat-human" in stored
    assert "computer-reach" in stored
    manager_prompt = "\n".join(
        str(m.get("content") or "")
        for c in rig.llm.calls
        if str(c["messages"][0]["content"]).startswith("You are the Manager")
        for m in c["messages"]
    )
    assert "[playbook: outreach]" in manager_prompt
    assert "Jian Hong" in manager_prompt
    assert "Matching playbooks are inlined below" in manager_prompt


@pytest.mark.asyncio
async def test_deny_tools_blocks_mcp_on_teammate(settings, crew_env) -> None:
    settings.master_computer_control = True
    store = CrewStore(settings.db_path)
    from CortexOS.crew.events import EventBus
    from CortexOS.crew.mcp_client import MCPManager

    bus = EventBus()
    mcp = MCPManager(settings.mcp_config_path, master_on=True)
    fake = FakeMCPClient("uacc", [{"name": "click", "inputSchema": {"type": "object"}}], armed=True)
    mcp.clients["uacc"] = fake
    llm = FakeLLM()
    runtime = CrewRuntime(store, bus, settings, mcp, FakeBridge(), llm_chat=llm)
    space = store.create_space("Desk")
    llm.manager.extend(
        [
            LLMResult(
                tool_calls=[
                    _tc(
                        "spawn_agent",
                        name="clicker",
                        brief="click it",
                        deny_tools=["click"],
                    )
                ]
            ),
            LLMResult(tool_calls=[_tc("wait_for_replies", timeout_seconds=2)]),
            LLMResult(text="Click was denied by the teammate grant."),
        ]
    )
    llm.teammate.extend(
        [
            LLMResult(tool_calls=[_tc("mcp_uacc_click", x=1, y=2)]),
            LLMResult(text="could not click"),
        ]
    )
    await runtime.on_user_message(space["id"], "click the button")
    await wait_run_done(runtime, space["id"])
    assert fake.called == []
    tools = [m for m in store.list_messages(space["id"]) if m["role"] == "tool"]
    assert tools and "denied" in tools[0]["content"].lower()
    answer = [m for m in store.list_messages(space["id"]) if m["role"] == "assistant"][-1]
    assert "denied" in answer["content"].lower()
    store.close()


@pytest.mark.asyncio
async def test_verify_without_criteria_skips_rubber_stamp(rig) -> None:
    space = rig.store.create_space("Gate")
    rig.llm.manager.extend(
        [
            LLMResult(
                tool_calls=[
                    _tc(
                        "spawn_agent",
                        name="writer",
                        capability="PRD",
                        brief="draft",
                        verify=True,
                    )
                ]
            ),
            LLMResult(tool_calls=[_tc("wait_for_replies", timeout_seconds=2)]),
            LLMResult(text="Draft done. No verifier: no criteria."),
        ]
    )
    rig.llm.teammate.append(LLMResult(text="PRD draft body"))
    await rig.runtime.on_user_message(space["id"], "write a PRD and verify it")
    await wait_run_done(rig.runtime, space["id"])
    names = [a["name"] for a in rig.store.list_agents(space["id"])]
    assert "writer" in names
    assert not any(n.endswith("-verify") for n in names)
    writer = rig.store.get_agent_by_name(space["id"], "writer")
    assert writer is not None
    assert int(writer.get("verify") or 0) == 0
    final = [m for m in rig.store.list_messages(space["id"]) if m["role"] == "assistant"][-1]
    assert "criteria" in final["content"].lower() or "draft" in final["content"].lower()


@pytest.mark.asyncio
async def test_verify_with_criteria_spawns_verifier(rig) -> None:
    space = rig.store.create_space("Gate")
    rig.llm.manager.extend(
        [
            LLMResult(
                tool_calls=[
                    _tc(
                        "spawn_agent",
                        name="writer",
                        capability="PRD",
                        brief="draft",
                        verify=True,
                        verify_criteria=["names the problem", "no fake metrics"],
                    )
                ]
            ),
            LLMResult(tool_calls=[_tc("wait_for_replies", timeout_seconds=3)]),
            LLMResult(text="Verifier reported passed=false on fake metrics."),
        ]
    )
    rig.llm.teammate.append(LLMResult(text="PRD draft with a number I invented."))
    rig.llm.teammate.append(LLMResult(text="passed: false. Invented metrics."))
    await rig.runtime.on_user_message(space["id"], "write a PRD and verify against acceptance")
    await wait_run_done(rig.runtime, space["id"])
    names = [a["name"] for a in rig.store.list_agents(space["id"])]
    assert "writer" in names
    assert "writer-verify" in names
    a2a = [m for m in rig.store.list_messages(space["id"]) if m["role"] == "agent"]
    assert any("passed: false" in m["content"] for m in a2a)
    final = [m for m in rig.store.list_messages(space["id"]) if m["role"] == "assistant"][-1]
    assert "passed" in final["content"].lower() or "verifier" in final["content"].lower()


@pytest.mark.asyncio
async def test_rename_agent_is_visible_on_roster(rig) -> None:
    space = rig.store.create_space("Build")
    rig.llm.manager.extend(
        [
            LLMResult(tool_calls=[_tc("spawn_agent", name="draft", brief="n")]),
            LLMResult(tool_calls=[_tc("wait_for_replies", timeout_seconds=2)]),
            LLMResult(tool_calls=[_tc("rename_agent", old_name="draft", new_name="sku-prd")]),
            LLMResult(text="Renamed draft to sku-prd."),
        ]
    )
    rig.llm.teammate.append(LLMResult(text="done"))
    await rig.runtime.on_user_message(space["id"], "write a PRD")
    await wait_run_done(rig.runtime, space["id"])
    names = [a["name"] for a in rig.store.list_agents(space["id"])]
    assert "sku-prd" in names
    assert "draft" not in names
    final = [m for m in rig.store.list_messages(space["id"]) if m["role"] == "assistant"][-1]
    assert "sku-prd" in final["content"]
