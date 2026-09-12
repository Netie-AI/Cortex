"""Scale = seat existing writers; Build = skill build + named verify.

Every assertion is on what the operator sees: the stored transcript, the
agent rows the HUD paints, assignments.json, and HTTP bodies (CLAUDE.md
section 8). A planner result alone certifies nothing, so the pure tests are
paired with runtime and HTTP tests that read the same artifacts back.
"""

from __future__ import annotations

import json
import time
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from CortexOS.crew import github as github_mod
from CortexOS.crew import scale
from CortexOS.crew.board import PACKS_DIR
from CortexOS.crew.llm import LLMResult, ToolCall
from CortexOS.crew.server import create_app
from tests.test_crew.conftest import FakeLLM, wait_run_done


@pytest.fixture()
def client(settings, crew_env) -> Iterator[SimpleNamespace]:
    fake = FakeLLM()
    app = create_app(settings, llm_chat=fake)
    with TestClient(app) as tc:
        yield SimpleNamespace(http=tc, llm=fake, app=app, crew=app.state.crew)


# -- pure planner -------------------------------------------------------------


def test_default_verify_cmd_matches_build_pack() -> None:
    """The pack names the command; the constant must not drift from it."""
    pack = (PACKS_DIR / "build.md").read_text(encoding="utf-8")
    assert scale.DEFAULT_VERIFY_CMD in pack


def test_job_name_and_parsers() -> None:
    assert scale.job_name("Netie-AI/Cortex#202") == "cortex-202"
    assert scale.job_name("https://github.com/Netie-AI/Cortex/issues/7") == "cortex-7"
    assert scale.job_name("FF-03") == ""
    assert scale.parse_build_rest("Netie-AI/Cortex#202") == (
        "Netie-AI/Cortex#202",
        "",
        scale.DEFAULT_VERIFY_CMD,
    )
    assert scale.parse_build_rest("Netie-AI/Cortex#202 | Scout | pytest tests/x -q") == (
        "Netie-AI/Cortex#202",
        "Scout",
        "pytest tests/x -q",
    )
    assert scale.parse_scale_rest("") == (scale.DEFAULT_SCALE_LIMIT, [])
    assert scale.parse_scale_rest("5") == (5, [])
    assert scale.parse_scale_rest("2 | Scout, Gate") == (2, ["Scout", "Gate"])
    assert scale.parse_scale_rest("Scout") == (scale.DEFAULT_SCALE_LIMIT, ["Scout"])
    assert scale.parse_scale_rest("all") == (scale.MAX_SCALE_LIMIT, [])
    assert scale.parse_scale_rest("999")[0] == scale.MAX_SCALE_LIMIT
    assert scale.parse_scale_rest("0")[0] == 1


def test_build_goal_and_criteria_name_the_command() -> None:
    goal = scale.build_goal("Netie-AI/Cortex#202", "grant catalog", "body text", "pytest -q")
    assert "Netie-AI/Cortex#202" in goal and "grant catalog" in goal and "body text" in goal
    assert "pytest -q" in goal and "skill build" in goal
    assert "do not claim green unless it ran" in goal
    crit = scale.build_criteria("pytest -q")
    assert any("pytest -q" in c for c in crit)
    assert any("no green claim" in c.lower() for c in crit)
    assert any("SEATED" in c for c in crit)


def _agent(
    name: str, status: str = "idle", mode: str = "active", alive: int = 1, stop_reason: str = ""
) -> dict:
    return {
        "name": name,
        "status": status,
        "mode": mode,
        "alive": alive,
        "stop_reason": stop_reason,
    }


def _tickets(*numbers: int) -> list[dict]:
    return [{"spec": f"Netie-AI/Cortex#{n}", "title": f"ticket {n}"} for n in numbers]


def test_plan_scale_seats_existing_idle_first_then_spawns_to_cap() -> None:
    agents = [
        _agent("Manager"),
        _agent("Scout"),  # idle -> reused first
        _agent("Gate", status="active"),  # busy -> skipped
        _agent("Ghost", status="stopped", alive=0, stop_reason="operator killed"),
        _agent("Watch", status="goal", mode="goal"),  # goal mode -> only when named
    ]
    plan = scale.plan_scale(_tickets(201, 202, 203, 204), agents, cap=8, limit=3)
    # 5 rows, cap 8: Scout reuse costs its verifier slot (1), a spawn costs
    # writer + verifier (2). 3 - 1 - 2 = 0, so #203 queues and #204 is over limit.
    assert [(s.spec, s.writer, s.reused) for s in plan.seats] == [
        ("Netie-AI/Cortex#201", "Scout", True),
        ("Netie-AI/Cortex#202", "cortex-202", False),
    ]
    assert plan.reused == 1 and plan.spawned == 1
    assert plan.queued[0][0] == "Netie-AI/Cortex#203"
    assert plan.queued[0][1].startswith(scale.REASON_CAP)
    assert plan.queued[1] == ("Netie-AI/Cortex#204", f"{scale.REASON_LIMIT} 3")
    assert ("Gate", "busy") in plan.skipped
    assert ("Ghost", "stopped (operator killed)") in plan.skipped
    assert ("Watch", "goal mode (name it to reuse)") in plan.skipped
    assert not any(who == "Manager" for who, _why in plan.skipped)
    public = plan.public()
    assert public["seats"][0]["writer"] == "Scout"
    assert "one agent per issue" in public["law"]


def test_plan_scale_named_writers_and_bound_rows() -> None:
    agents = [_agent("Manager"), _agent("Scout"), _agent("Watch", status="goal", mode="goal")]
    plan = scale.plan_scale(
        _tickets(201, 202),
        agents,
        cap=8,
        limit=2,
        names=["Watch", "Nobody"],
        bound={"Scout": "Netie-AI/Cortex#150"},
    )
    assert [(s.spec, s.writer, s.reused) for s in plan.seats] == [
        ("Netie-AI/Cortex#201", "Watch", True),
        ("Netie-AI/Cortex#202", "cortex-202", False),
    ]
    assert ("Nobody", "not in this space") in plan.skipped
    # Unnamed pass: the bound writer is skipped with the ticket it already holds.
    unnamed = scale.plan_scale(_tickets(201), agents, cap=8, bound={"Scout": "Netie-AI/Cortex#150"})
    assert ("Scout", f"{scale.REASON_BOUND} Netie-AI/Cortex#150") in unnamed.skipped
    # A job name that already exists in the roster is reused, not spawned twice.
    again = scale.plan_scale(_tickets(202), [_agent("Manager"), _agent("cortex-202")], cap=8)
    assert [(s.writer, s.reused) for s in again.seats] == [("cortex-202", True)]
    # A ticket already bound elsewhere never seats a second writer.
    held = scale.plan_scale(_tickets(150), agents, cap=8, bound={"Scout": "Netie-AI/Cortex#150"})
    assert held.seats == [] and held.queued == [("Netie-AI/Cortex#150", "already bound")]
    # Verifier rows carry the Gate role; they are never seated as writers.
    gated = scale.plan_scale(_tickets(201), [_agent("Manager"), _agent("Scout-verify")], cap=8)
    assert [(s.writer, s.reused) for s in gated.seats] == [("cortex-201", False)]
    assert ("Scout-verify", "verifier row, not a writer") in gated.skipped


def test_plan_scale_cap_leaves_no_verifier_slot() -> None:
    agents = [_agent("Manager"), _agent("Scout"), _agent("A"), _agent("B"), _agent("C")]
    plan = scale.plan_scale(_tickets(201, 202), agents, cap=5, limit=2)
    assert plan.seats == []
    assert all(why.startswith(scale.REASON_CAP) for _spec, why in plan.queued)
    assert len(plan.queued) == 2


def test_render_scale_counts_only_successful_binds() -> None:
    plan = scale.plan_scale(
        _tickets(201, 202, 203), [_agent("Manager"), _agent("Scout")], cap=8, limit=2
    )
    outcomes = [
        (plan.seats[0], True, "Build Netie-AI/Cortex#201 -> Scout"),
        (plan.seats[1], False, "DENIED: spawn failed"),
    ]
    text = scale.render_scale(plan, outcomes)
    assert text.startswith("Scaled 1/3 ready tickets: reused 1 existing, spawned 0 job-named")
    assert "- FAILED Netie-AI/Cortex#202 -> cortex-202 (spawned): DENIED: spawn failed" in text
    assert "- queued Netie-AI/Cortex#203: over limit 2" in text
    assert "never" not in text.lower() or "one agent per issue" in text


def test_ready_tickets_drops_seated_bound_and_non_issue_rows(tmp_path: Path, monkeypatch) -> None:
    claims = tmp_path / "CLAIMS.json"
    claims.write_text(
        '{"tickets":[{"ticket":"Netie-AI/Cortex#128","owner_pr":"Netie-AI/Cortex#128","role":"SEATED"}]}',
        encoding="utf-8",
    )
    monkeypatch.setenv("CREW_CLAIMS", str(claims))
    github_mod.remember_fetched(
        tmp_path,
        {
            "ok": True,
            "issues": [
                {"spec": "Netie-AI/Cortex#128", "title": "seated", "seated": False},
                {"spec": "Netie-AI/Cortex#150", "title": "bound elsewhere"},
                {"spec": "Netie-AI/Cortex#202", "title": "grant catalog"},
                {"spec": "Netie-AI/Cortex#202", "title": "duplicate"},
                {"spec": "Netie-AI/Cortex#203", "title": "jail", "seated": True},
                {"spec": "FF-03", "title": "not an issue spec"},
            ],
        },
    )
    rows = scale.ready_tickets(tmp_path, bound_specs={"Netie-AI/Cortex#150"})
    assert rows == [{"spec": "Netie-AI/Cortex#202", "title": "grant catalog"}]


# -- runtime: operator-visible flow ------------------------------------------


def _tc(tool: str, **args: object) -> ToolCall:
    return ToolCall(id=f"c-{tool}", name=tool, args=dict(args))


def _fake_show_issue(spec: str, **_k: object) -> dict:
    return {
        "ok": True,
        "spec": spec,
        "title": f"title for {spec}",
        "body": "Acceptance: one named verify command.",
        "state": "OPEN",
        "seated": False,
        "ready": True,
        "detail": "",
        "law": "Read only.",
    }


@pytest.mark.asyncio
async def test_build_slash_binds_skill_verifier_and_paints_verdict(
    rig, tmp_path, monkeypatch
) -> None:
    claims = tmp_path / "CLAIMS.json"
    claims.write_text(
        '{"tickets":[{"ticket":"Netie-AI/Cortex#128","owner_pr":"Netie-AI/Cortex#128","role":"SEATED"}]}',
        encoding="utf-8",
    )
    monkeypatch.setenv("CREW_CLAIMS", str(claims))
    monkeypatch.setattr(github_mod, "show_issue", _fake_show_issue)
    space = rig.store.create_space("HQ")

    # Bare /build still loads the pack; nothing is bound.
    bare = await rig.runtime.on_user_message(space["id"], "/build")
    assert bare.get("run_id") is None
    loaded = rig.store.list_messages(space["id"])[-1]
    assert loaded["role"] == "system"
    assert "Skill /build loaded" in loaded["content"] and "Ponytail" in loaded["content"]

    # SEATED refuses before any spawn.
    seated = await rig.runtime.on_user_message(space["id"], "/build Netie-AI/Cortex#128")
    assert seated.get("run_id") is None
    deny = [m for m in rig.store.list_messages(space["id"]) if m["role"] == "tool"][-1]
    assert deny["content"].startswith("DENIED") and "SEATED" in deny["content"]
    assert rig.store.get_agent_by_name(space["id"], "cortex-128") is None

    rig.llm.manager.extend(
        [
            LLMResult(tool_calls=[_tc("wait_for_replies", timeout_seconds=8)]),
            LLMResult(tool_calls=[_tc("wait_for_replies", timeout_seconds=8)]),
            LLMResult(text="Build reported; verifier reported. Nothing merged."),
        ]
    )
    rig.llm.teammate.extend(
        [
            LLMResult(text="Ran python -m pytest tests/test_crew -q: 275 passed (summary only)."),
            LLMResult(text="passed: false. Output was summarised, not pasted. Not green."),
        ]
    )
    posted = await rig.runtime.on_user_message(space["id"], "/build Netie-AI/Cortex#202")
    assert posted.get("run_id")
    assert posted.get("command") == "build"
    await wait_run_done(rig.runtime, space["id"], timeout=15.0)

    tool = [
        m
        for m in rig.store.list_messages(space["id"])
        if m["role"] == "tool" and (m.get("meta") or {}).get("tool") == "build_issue"
    ][-1]
    assert tool["content"].startswith("Build Netie-AI/Cortex#202 -> cortex-202")
    assert scale.DEFAULT_VERIFY_CMD in tool["content"]
    assert "Did not write CLAIMS.json" in tool["content"]

    worker = rig.store.get_agent_by_name(space["id"], "cortex-202")
    assert worker is not None
    assert int(worker["verify"]) == 1
    assert scale.DEFAULT_VERIFY_CMD in worker["verify_criteria"]
    assert "build" in json.loads(worker["skills"])
    assert "Skill build:" in worker["role_prompt"] and "Ponytail" in worker["role_prompt"]
    assert worker["mode"] == "goal"
    assert "Netie-AI/Cortex#202" in worker["goal_text"]
    assert scale.DEFAULT_VERIFY_CMD in worker["goal_text"]
    assert "Acceptance: one named verify command." in worker["goal_text"]

    verifier = rig.store.get_agent_by_name(space["id"], "cortex-202-verify")
    assert verifier is not None
    assert verifier["capability"] == "Gate"
    assert scale.DEFAULT_VERIFY_CMD in verifier["role_prompt"]

    painted = " ".join(str(m.get("content") or "") for m in rig.store.list_messages(space["id"]))
    assert "275 passed (summary only)" in painted
    assert "passed: false. Output was summarised, not pasted. Not green." in painted
    assert "Build reported; verifier reported." in painted
    assert "Cut off" not in painted

    from CortexOS.crew.assign import public as assignment_public

    rows = assignment_public(rig.settings.data_dir)
    assert rows[0]["spec"] == "Netie-AI/Cortex#202" and rows[0]["agent"] == "cortex-202"
    assert claims.read_text(encoding="utf-8").count("SEATED") == 1  # CLAIMS untouched


@pytest.mark.asyncio
async def test_build_slash_reuses_named_teammate_with_custom_verify(
    rig, tmp_path, monkeypatch
) -> None:
    claims = tmp_path / "CLAIMS.json"
    claims.write_text('{"tickets":[]}', encoding="utf-8")
    monkeypatch.setenv("CREW_CLAIMS", str(claims))
    monkeypatch.setattr(github_mod, "show_issue", _fake_show_issue)
    space = rig.store.create_space("HQ")
    rig.runtime.ensure_manager(space["id"])
    scout = rig.store.upsert_agent(space["id"], "Scout", role_prompt="Scout the brief.")
    rig.llm.manager.extend(
        [
            LLMResult(tool_calls=[_tc("wait_for_replies", timeout_seconds=8)]),
            LLMResult(tool_calls=[_tc("wait_for_replies", timeout_seconds=8)]),
            LLMResult(text="Scout built it."),
        ]
    )
    rig.llm.teammate.extend(
        [
            LLMResult(text="pytest tests/test_execution -q\n201 passed in 3.1s"),
            LLMResult(text="passed: true. Output pasted; 201 passed."),
        ]
    )
    posted = await rig.runtime.on_user_message(
        space["id"], "/build Netie-AI/Cortex#203 | Scout | pytest tests/test_execution -q"
    )
    assert posted.get("run_id")
    await wait_run_done(rig.runtime, space["id"], timeout=15.0)
    row = rig.store.get_agent(scout["id"])
    assert row is not None
    assert int(row["verify"]) == 1
    assert "pytest tests/test_execution -q" in row["verify_criteria"]
    assert row["role_prompt"].startswith("Scout the brief.")
    assert row["role_prompt"].count("Skill build:") == 1
    assert "pytest tests/test_execution -q" in row["goal_text"]
    tool = [
        m
        for m in rig.store.list_messages(space["id"])
        if m["role"] == "tool" and (m.get("meta") or {}).get("tool") == "build_issue"
    ][-1]
    assert "-> Scout" in tool["content"]
    assert "verify: pytest tests/test_execution -q" in tool["content"]
    painted = " ".join(str(m.get("content") or "") for m in rig.store.list_messages(space["id"]))
    assert "201 passed in 3.1s" in painted
    assert "passed: true. Output pasted" in painted
    assert rig.store.get_agent_by_name(space["id"], "Scout-verify") is not None
    # Manager stays out of the writer pool.
    denied = await rig.runtime.on_user_message(space["id"], "/build Netie-AI/Cortex#204 | Manager")
    assert denied.get("run_id") is None
    deny = [m for m in rig.store.list_messages(space["id"]) if m["role"] == "tool"][-1]
    assert "not Manager" in deny["content"]


@pytest.mark.asyncio
async def test_scale_slash_seats_idle_first_spawns_to_cap_and_refuses_empty(
    rig, tmp_path, monkeypatch
) -> None:
    claims = tmp_path / "CLAIMS.json"
    claims.write_text(
        '{"tickets":[{"ticket":"Netie-AI/Cortex#128","owner_pr":"Netie-AI/Cortex#128","role":"SEATED"}]}',
        encoding="utf-8",
    )
    monkeypatch.setenv("CREW_CLAIMS", str(claims))
    monkeypatch.setattr(github_mod, "show_issue", _fake_show_issue)
    space = rig.store.create_space("HQ")

    # Nothing fetched yet: refuse and say what to do.
    empty = await rig.runtime.on_user_message(space["id"], "/scale")
    assert empty.get("run_id") is None
    deny = [m for m in rig.store.list_messages(space["id"]) if m["role"] == "tool"][-1]
    assert deny["content"].startswith("DENIED: no ready tickets") and "/fetch" in deny["content"]

    github_mod.remember_fetched(
        rig.settings.data_dir,
        {
            "ok": True,
            "issues": [
                {"spec": "Netie-AI/Cortex#128", "title": "seated"},
                {"spec": "Netie-AI/Cortex#202", "title": "grant catalog"},
                {"spec": "Netie-AI/Cortex#203", "title": "jail"},
                {"spec": "Netie-AI/Cortex#204", "title": "dialog"},
            ],
        },
    )
    rig.store.upsert_agent(space["id"], "Scout", role_prompt="Scout the brief.")
    rig.store.upsert_agent(space["id"], "Gate", role_prompt="Gate.")
    rig.runtime.ensure_manager(space["id"])
    gate = rig.store.get_agent_by_name(space["id"], "Gate")
    rig.runtime.wait_agent(gate["id"])  # operator-parked waiting: not a free writer

    rig.llm.manager.extend(
        [
            LLMResult(tool_calls=[_tc("wait_for_replies", timeout_seconds=8)]),
            LLMResult(tool_calls=[_tc("wait_for_replies", timeout_seconds=8)]),
            LLMResult(tool_calls=[_tc("wait_for_replies", timeout_seconds=8)]),
            LLMResult(tool_calls=[_tc("wait_for_replies", timeout_seconds=8)]),
            LLMResult(text="Two builds reported and verified. Nothing merged."),
        ]
    )
    rig.llm.teammate.extend(
        [
            LLMResult(text="worker report A: output pasted below\n275 passed"),
            LLMResult(text="worker report B: output pasted below\n275 passed"),
            LLMResult(text="verifier verdict: passed: true (A)"),
            LLMResult(text="verifier verdict: passed: true (B)"),
        ]
    )
    posted = await rig.runtime.on_user_message(space["id"], "/scale 2")
    assert posted.get("run_id")
    assert posted.get("command") == "scale"
    await wait_run_done(rig.runtime, space["id"], timeout=20.0)

    tool = [
        m
        for m in rig.store.list_messages(space["id"])
        if m["role"] == "tool" and (m.get("meta") or {}).get("tool") == "scale_tickets"
    ][-1]
    text = tool["content"]
    assert text.startswith("Scaled 2/3 ready tickets: reused 1 existing, spawned 1 job-named")
    assert "- Netie-AI/Cortex#202 -> Scout (existing): Build Netie-AI/Cortex#202 -> Scout" in text
    assert (
        "- Netie-AI/Cortex#203 -> cortex-203 (spawned): Build Netie-AI/Cortex#203 -> cortex-203"
        in text
    )
    assert "- queued Netie-AI/Cortex#204: over limit 2" in text
    assert "- skipped Gate: parked waiting (name it to reuse)" in text
    assert "Netie-AI/Cortex#128" not in text
    assert "one agent per issue" in text
    args = tool["meta"]["args"]
    assert [s["writer"] for s in args["seated"]] == ["Scout", "cortex-203"]
    assert args["queued"] == [{"spec": "Netie-AI/Cortex#204", "reason": "over limit 2"}]

    from CortexOS.crew.assign import public as assignment_public

    rows = {r["spec"]: r["agent"] for r in assignment_public(rig.settings.data_dir)}
    assert rows == {"Netie-AI/Cortex#202": "Scout", "Netie-AI/Cortex#203": "cortex-203"}
    names = {a["name"] for a in rig.store.list_agents(space["id"])}
    assert {"Scout", "Gate", "cortex-203", "Scout-verify", "cortex-203-verify"} <= names
    assert "cortex-202" not in names  # Scout was reused instead of a fresh spawn
    assert "cortex-204" not in names  # over limit: not seated, not spawned
    for name in ("Scout", "cortex-203"):
        row = rig.store.get_agent_by_name(space["id"], name)
        assert int(row["verify"]) == 1
        assert scale.DEFAULT_VERIFY_CMD in row["verify_criteria"]
        assert row["mode"] == "goal"
    painted = " ".join(str(m.get("content") or "") for m in rig.store.list_messages(space["id"]))
    for expected in (
        "worker report A",
        "worker report B",
        "verifier verdict: passed: true (A)",
        "verifier verdict: passed: true (B)",
        "Two builds reported and verified.",
    ):
        assert expected in painted
    assert "Cut off" not in painted
    assert claims.read_text(encoding="utf-8").count("SEATED") == 1

    # Second round: #202/#203 are bound, every teammate is bound, waiting, or a
    # verifier row, and the cap (6 rows) leaves no writer + verifier slot for #204.
    # Nothing seats, nothing spawns, and the reason is in the transcript.
    rig.settings.max_agents_per_space = 6
    again = await rig.runtime.on_user_message(space["id"], "/scale 2")
    assert again.get("run_id") is None
    report = [m for m in rig.store.list_messages(space["id"]) if m["role"] == "tool"][-1]
    assert report["content"].startswith("Scaled 0/1 ready tickets: reused 0 existing, spawned 0")
    assert (
        "- queued Netie-AI/Cortex#204: cap reached (6 rows; writer + verifier slots)"
        in report["content"]
    )
    assert "- skipped Scout: bound to Netie-AI/Cortex#202" in report["content"]
    assert "- skipped Scout-verify: verifier row, not a writer" in report["content"]
    assert "cortex-204" not in {a["name"] for a in rig.store.list_agents(space["id"])}
    assert not rig.runtime._space_run.get(space["id"])


@pytest.mark.asyncio
async def test_verifier_cap_skip_is_visible(rig, tmp_path, monkeypatch) -> None:
    """A verifier that cannot spawn says so in the transcript. Silent skip = fake green."""
    claims = tmp_path / "CLAIMS.json"
    claims.write_text('{"tickets":[]}', encoding="utf-8")
    monkeypatch.setenv("CREW_CLAIMS", str(claims))
    monkeypatch.setattr(github_mod, "show_issue", _fake_show_issue)
    rig.settings.max_agents_per_space = 2  # Manager + one worker: no room for a verifier
    space = rig.store.create_space("HQ")
    rig.llm.manager.extend(
        [
            LLMResult(tool_calls=[_tc("wait_for_replies", timeout_seconds=8)]),
            LLMResult(text="Worker reported. Verifier could not spawn."),
        ]
    )
    rig.llm.teammate.append(LLMResult(text="worker done; claims green"))
    posted = await rig.runtime.on_user_message(space["id"], "/build Netie-AI/Cortex#205")
    assert posted.get("run_id")
    await wait_run_done(rig.runtime, space["id"], timeout=15.0)
    painted = " ".join(str(m.get("content") or "") for m in rig.store.list_messages(space["id"]))
    assert "worker done; claims green" in painted
    assert (
        "DENIED: verify skipped for cortex-205: agent cap reached (2). Not verified; not green."
        in painted
    )
    assert rig.store.get_agent_by_name(space["id"], "cortex-205-verify") is None


# -- HTTP + HUD ---------------------------------------------------------------


def _wait_http_run(client, space_id: str, timeout: float = 20.0) -> None:
    deadline = time.time() + timeout
    while client.crew.runtime._space_run.get(space_id):
        if time.time() > deadline:
            raise AssertionError("run did not finish")
        time.sleep(0.05)


def test_http_build_and_scale_endpoints(client, tmp_path, monkeypatch) -> None:
    claims = tmp_path / "CLAIMS.json"
    claims.write_text(
        '{"tickets":[{"ticket":"Netie-AI/Cortex#128","owner_pr":"Netie-AI/Cortex#128","role":"SEATED"}]}',
        encoding="utf-8",
    )
    monkeypatch.setenv("CREW_CLAIMS", str(claims))
    monkeypatch.setattr(github_mod, "show_issue", _fake_show_issue)
    space = client.http.post("/crew/spaces", json={"title": "HQ"}).json()

    seated = client.http.post(
        "/crew/tickets/build", json={"spec": "Netie-AI/Cortex#128", "space_id": space["id"]}
    )
    assert seated.status_code == 409 and "SEATED" in seated.json()["detail"]
    bad = client.http.post("/crew/tickets/build", json={"spec": "FF-03", "space_id": space["id"]})
    assert bad.status_code == 400 and "/build owner/repo#n" in bad.json()["detail"]
    missing = client.http.post(
        "/crew/tickets/build", json={"spec": "Netie-AI/Cortex#1", "space_id": "nope"}
    )
    assert missing.status_code == 404
    empty = client.http.post("/crew/tickets/scale", json={"space_id": space["id"]})
    assert empty.status_code == 409 and "no ready tickets" in empty.json()["detail"]

    client.llm.manager.extend(
        [
            LLMResult(tool_calls=[_tc("wait_for_replies", timeout_seconds=8)]),
            LLMResult(tool_calls=[_tc("wait_for_replies", timeout_seconds=8)]),
            LLMResult(text="Built and verified over HTTP."),
        ]
    )
    client.llm.teammate.extend(
        [
            LLMResult(text="worker: pasted output\n275 passed"),
            LLMResult(text="verifier: passed: true"),
        ]
    )
    built = client.http.post(
        "/crew/tickets/build",
        json={
            "spec": "Netie-AI/Cortex#202",
            "space_id": space["id"],
            "name": "Scout",
            "verify": "pytest -q",
        },
    )
    assert built.status_code == 200
    body = built.json()
    assert body["ok"] is True and body["run_id"]
    assert body["verify"] == "pytest -q"
    assert body["detail"].startswith("Build Netie-AI/Cortex#202 -> Scout")
    assert "CLAIMS" in body["law"]
    _wait_http_run(client, space["id"])
    board = client.http.get("/crew/tickets").json()
    assert board["assignments"][0] == {
        "spec": "Netie-AI/Cortex#202",
        "agent": "Scout",
        "space_id": space["id"],
        "title": "title for Netie-AI/Cortex#202",
        "ready": True,
    }
    agents = client.http.get(f"/crew/spaces/{space['id']}/agents").json()
    by_name = {a["name"]: a for a in agents}
    assert "Scout" in by_name and "Scout-verify" in by_name
    painted = " ".join(
        str(m.get("content") or "")
        for m in client.http.get(f"/crew/spaces/{space['id']}/messages").json()
    )
    assert "worker: pasted output" in painted and "verifier: passed: true" in painted

    github_mod.remember_fetched(
        client.crew.settings.data_dir,
        {
            "ok": True,
            "issues": [
                {"spec": "Netie-AI/Cortex#202", "title": "already bound"},
                {"spec": "Netie-AI/Cortex#203", "title": "jail"},
                {"spec": "Netie-AI/Cortex#204", "title": "dialog"},
            ],
        },
    )
    client.llm.manager.extend(
        [
            LLMResult(tool_calls=[_tc("wait_for_replies", timeout_seconds=8)]),
            LLMResult(tool_calls=[_tc("wait_for_replies", timeout_seconds=8)]),
            LLMResult(text="Scaled one."),
        ]
    )
    client.llm.teammate.extend(
        [
            LLMResult(text="worker 203: pasted\n275 passed"),
            LLMResult(text="verifier 203: passed: true"),
        ]
    )
    scaled = client.http.post(
        "/crew/tickets/scale", json={"space_id": space["id"], "limit": 1, "names": "Scout"}
    )
    assert scaled.status_code == 200
    plan = scaled.json()
    # Scout is bound to #202, so the named reuse is skipped and #203 gets a job-named spawn.
    assert plan["skipped"] == [{"writer": "Scout", "reason": "bound to Netie-AI/Cortex#202"}]
    assert [s["writer"] for s in plan["seated"]] == ["cortex-203"]
    assert plan["queued"] == [{"spec": "Netie-AI/Cortex#204", "reason": "over limit 1"}]
    assert plan["run_id"]
    assert "one agent per issue" in plan["law"]
    _wait_http_run(client, space["id"])
    belt = client.http.get("/v1/belt").json()
    assigned = {a["spec"]: a["agent"] for a in belt["assignments"]}
    assert assigned == {"Netie-AI/Cortex#202": "Scout", "Netie-AI/Cortex#203": "cortex-203"}


def test_hud_paints_build_and_scale_controls(client) -> None:
    page = client.http.get("/")
    assert page.status_code == 200
    html = page.text
    assert 'id="ticketsScale"' in html
    assert "data-build=" in html
    assert "/crew/tickets/build" in html and "/crew/tickets/scale" in html
    assert "never one agent per issue" in html
    commands = client.http.get("/crew/commands").json()["commands"]
    by_slash = {c["slash"]: c for c in commands}
    assert by_slash["build"]["kind"] == "desk" and by_slash["build"]["action"] == "build_issue"
    assert by_slash["scale"]["kind"] == "desk" and by_slash["scale"]["action"] == "scale_tickets"
    assert "implement" in by_slash["build"]["aliases"]
    assert "seat" in by_slash["scale"]["aliases"]
