"""Jailed workspace, todos, compact, load_skill - transcript-visible."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from CortexOS.crew.llm import LLMError, LLMResult, ToolCall
from CortexOS.crew.store import CrewStore
from CortexOS.crew.workspace import SpaceWorkspace, WorkspaceError


def _tc(tool: str, **args: object) -> ToolCall:
    return ToolCall(id=f"c-{tool}", name=tool, args=dict(args))


async def wait_run_done(runtime: object, space_id: str, timeout: float = 10.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while runtime._space_run.get(space_id):  # type: ignore[attr-defined]
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError("run did not finish in time")
        await asyncio.sleep(0.01)


def test_workspace_jail_blocks_escape(tmp_path: Path) -> None:
    ws = SpaceWorkspace(tmp_path / "ws")
    ws.write("ok.md", "hello")
    assert "hello" in ws.read("ok.md")
    try:
        ws.resolve("../secret")
        raise AssertionError("escape should fail")
    except WorkspaceError as exc:
        assert "escape" in str(exc)
    try:
        ws.write("", "x")
        raise AssertionError("empty path should fail")
    except WorkspaceError:
        pass
    ws.write("notes/a.md", "alpha")
    ws.edit("notes/a.md", "alpha", "beta")
    assert "beta" in ws.read("notes/a.md")
    listed = ws.glob("*.md")
    assert "ok.md" in listed


def test_todos_replace_and_one_in_progress(tmp_path: Path) -> None:
    store = CrewStore(tmp_path / "crew.db")
    space = store.create_space("HQ")
    rows = store.replace_todos(
        space["id"],
        [
            {"content": "read ticket", "status": "completed"},
            {"content": "write patch", "status": "in_progress"},
            {"content": "also in progress", "status": "in_progress"},
            {"content": "verify", "status": "pending"},
        ],
    )
    assert [r["status"] for r in rows] == ["completed", "in_progress", "pending", "pending"]
    text = store.render_todos(space["id"])
    assert "[x] read ticket" in text
    assert "[>] write patch" in text
    assert "[ ] also in progress" in text
    store.close()


def test_new_space_seeds_idle_roster(rig) -> None:
    space = rig.store.create_space("HQ")
    names = {a["name"] for a in rig.runtime.ensure_roster(space["id"])}
    assert "Manager" in names
    assert "PRD" in names
    assert "Ticket" in names
    idle = [a for a in rig.store.list_agents(space["id"]) if a["name"] != "Manager"]
    assert idle
    assert all(a.get("status") == "idle" for a in idle)


@pytest.mark.asyncio
async def test_run_path_does_not_seed_the_idle_roster(rig) -> None:
    """Seeding is desk display. A run must not inherit ten idle specialists.

    Regression: on_user_message seeded the roster, so broadcast fanned out to
    teammates the Manager never spawned.
    """
    space = rig.store.create_space("HQ")
    rig.llm.manager.append(LLMResult(text="pong"))
    await rig.runtime.on_user_message(space["id"], "reply pong, do not spawn")
    await wait_run_done(rig.runtime, space["id"])
    assert [a["name"] for a in rig.store.list_agents(space["id"])] == ["Manager"]


def test_belt_json_does_not_decide_work_shape(tmp_path: Path, monkeypatch) -> None:
    claims = tmp_path / "CLAIMS.json"
    claims.write_text(
        '{"tickets":[{"ticket":"ANS-01","role":"SEATED","may_write":true,"repo":"Netie-AI/Cortex"}]}',
        encoding="utf-8",
    )
    monkeypatch.setenv("CREW_CLAIMS", str(claims))
    from CortexOS.crew.belt import snapshot as belt_snapshot

    body = belt_snapshot(cortex={"ok": True})
    assert body["bus"] == "github-issues"
    assert body["plan_for_next"]["decides_work_shape"] is False
    assert body["converse"] is True
    assert body["tickets"]["items"][0]["number"] == "ANS-01"
    assert body["wakes"] == []
    assert body["confirms"] == []
    assert body["queue"] == {}


@pytest.mark.asyncio
async def test_write_todos_and_workspace_land_in_transcript(rig) -> None:
    space = rig.store.create_space("Build")
    rig.llm.manager.extend(
        [
            LLMResult(
                tool_calls=[
                    _tc(
                        "write_todos",
                        todos=[
                            {"content": "draft the PRD", "status": "in_progress"},
                            {"content": "gate it", "status": "pending"},
                        ],
                    )
                ]
            ),
            LLMResult(
                tool_calls=[_tc("write_file", path="prd.md", content="problem: missing harness")]
            ),
            LLMResult(tool_calls=[_tc("read_file", path="prd.md")]),
            LLMResult(text="PRD drafted in the workspace. Two-item plan is live."),
        ]
    )
    await rig.runtime.on_user_message(space["id"], "write a PRD and keep a plan")
    await wait_run_done(rig.runtime, space["id"])
    tools = [m for m in rig.store.list_messages(space["id"]) if m["role"] == "tool"]
    bodies = "\n".join(m["content"] for m in tools)
    assert "[>] draft the PRD" in bodies
    assert "wrote prd.md" in bodies
    assert "problem: missing harness" in bodies
    todos = rig.store.list_todos(space["id"])
    assert todos[0]["content"] == "draft the PRD"
    answer = [m for m in rig.store.list_messages(space["id"]) if m["role"] == "assistant"][-1]
    assert "PRD drafted" in answer["content"]
    assert "plan" in answer["content"].lower()


@pytest.mark.asyncio
async def test_workspace_escape_is_denied_in_transcript(rig) -> None:
    space = rig.store.create_space("Safe")
    rig.llm.manager.extend(
        [
            LLMResult(tool_calls=[_tc("read_file", path="../secret.txt")]),
            LLMResult(text="Path was denied. Workspace stays jailed."),
        ]
    )
    await rig.runtime.on_user_message(space["id"], "read the secret")
    await wait_run_done(rig.runtime, space["id"])
    tools = [m for m in rig.store.list_messages(space["id"]) if m["role"] == "tool"]
    assert tools and "DENIED" in tools[0]["content"]
    answer = [m for m in rig.store.list_messages(space["id"]) if m["role"] == "assistant"][-1]
    assert "denied" in answer["content"].lower() or "jailed" in answer["content"].lower()


@pytest.mark.asyncio
async def test_load_skill_returns_body(rig) -> None:
    space = rig.store.create_space("Teach")
    rig.llm.manager.extend(
        [
            LLMResult(tool_calls=[_tc("load_skill", name="tone")]),
            LLMResult(text="Tone skill loaded: ASCII only."),
        ]
    )
    await rig.runtime.on_user_message(space["id"], "load the tone skill")
    await wait_run_done(rig.runtime, space["id"])
    tools = [m for m in rig.store.list_messages(space["id"]) if m["role"] == "tool"]
    assert tools and "ASCII" in tools[0]["content"]
    answer = [m for m in rig.store.list_messages(space["id"]) if m["role"] == "assistant"][-1]
    assert "ASCII" in answer["content"]


@pytest.mark.asyncio
async def test_save_skill_lands_labeled_file_the_roster_can_load(rig) -> None:
    space = rig.store.create_space("Ingest")
    rig.llm.manager.extend(
        [
            LLMResult(
                tool_calls=[
                    _tc(
                        "save_skill",
                        name="impeccable",
                        body="Design rules. Contrast first.",
                        labels=["design-rules"],
                        source="https://github.com/pbakaus/impeccable",
                    )
                ]
            ),
            LLMResult(text="Saved impeccable under design-rules."),
        ]
    )
    await rig.runtime.on_user_message(space["id"], "Add impeccable skill into skill storage")
    await wait_run_done(rig.runtime, space["id"])
    tools = [m for m in rig.store.list_messages(space["id"]) if m["role"] == "tool"]
    assert tools and "impeccable" in tools[0]["content"]
    assert "design-rules" in tools[0]["content"]
    path = rig.settings.data_dir / "skills" / "impeccable.md"
    assert path.is_file()
    body = path.read_text(encoding="utf-8")
    assert "design-rules" in body
    assert "pbakaus/impeccable" in body
    answer = [m for m in rig.store.list_messages(space["id"]) if m["role"] == "assistant"][-1]
    assert "impeccable" in answer["content"].lower()


@pytest.mark.asyncio
async def test_web_search_is_offered_and_returns_hits(rig, monkeypatch) -> None:
    from CortexOS.crew import research

    monkeypatch.setattr(
        research,
        "web_search",
        lambda query, max_results=6: {
            "ok": True,
            "query": query,
            "results": [
                {
                    "title": "pbakaus/impeccable",
                    "url": "https://github.com/pbakaus/impeccable",
                    "snippet": "Design rules skill",
                }
            ],
        },
    )
    space = rig.store.create_space("Search")
    manager = rig.runtime.ensure_manager(space["id"])
    offered = {s["function"]["name"] for s in rig.runtime._toolspecs(True, manager)}
    assert {
        "web_search",
        "web_fetch",
        "github_search",
        "save_skill",
        "ingest_named_skill",
        "analog_clone",
    } <= offered
    rig.llm.manager.extend(
        [
            LLMResult(tool_calls=[_tc("web_search", query="impeccable github skill")]),
            LLMResult(text="Found pbakaus/impeccable on GitHub."),
        ]
    )
    await rig.runtime.on_user_message(space["id"], "Add impeccable skill into skill storage")
    await wait_run_done(rig.runtime, space["id"])
    tools = [m for m in rig.store.list_messages(space["id"]) if m["role"] == "tool"]
    assert tools and "pbakaus/impeccable" in tools[0]["content"]
    answer = [m for m in rig.store.list_messages(space["id"]) if m["role"] == "assistant"][-1]
    assert "impeccable" in answer["content"].lower()


@pytest.mark.asyncio
async def test_ingest_fallback_saves_when_model_hop_dies(rig, monkeypatch) -> None:
    from CortexOS.crew import research

    monkeypatch.setattr(
        research,
        "github_search",
        lambda query, limit=8: {
            "ok": True,
            "repos": [
                {
                    "name": "impeccable",
                    "full_name": "pbakaus/impeccable",
                    "url": "https://github.com/pbakaus/impeccable",
                    "description": "Design rules",
                }
            ],
            "web": [],
        },
    )
    monkeypatch.setattr(research, "web_search", lambda query, max_results=6: {"ok": True, "results": []})
    monkeypatch.setattr(
        research,
        "web_fetch",
        lambda url, max_chars=8000: {"ok": True, "text": "Impeccable is a design skill."},
    )

    async def boom(*_a, **_k):
        raise LLMError("OpenVault HTTP 500: Internal Server Error")

    rig.runtime._llm = boom
    space = rig.store.create_space("Fallback")
    await rig.runtime.on_user_message(space["id"], "Add impeccable skill into skill storage")
    await wait_run_done(rig.runtime, space["id"])
    answer = [m for m in rig.store.list_messages(space["id"]) if m["role"] == "assistant"][-1]
    assert "impeccable" in answer["content"].lower()
    assert "design-rules" in answer["content"]
    path = rig.settings.data_dir / "skills" / "impeccable.md"
    assert path.is_file()
    assert "pbakaus/impeccable" in path.read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_analog_fallback_writes_when_model_hop_dies(rig, monkeypatch) -> None:
    from CortexOS.crew import analog as analog_mod
    from CortexOS.crew import research
    from CortexOS.crew.llm import LLMError

    monkeypatch.setattr(
        research,
        "web_fetch",
        lambda url, max_chars=16000: {
            "ok": True,
            "url": url,
            "title": "Igloo",
            "text": "color: #1a1714; background: #0b0d10;",
        },
    )
    monkeypatch.setattr(
        research,
        "github_search",
        lambda query, limit=5: {"ok": True, "repos": [], "web": []},
    )
    monkeypatch.setattr(
        research,
        "web_search",
        lambda query, max_results=5: {"ok": True, "results": []},
    )
    monkeypatch.setattr(analog_mod, "_open_local", lambda _p: False)

    async def boom(*_a, **_k):
        raise LLMError("OpenVault HTTP 500: Internal Server Error")

    rig.runtime._llm = boom
    space = rig.store.create_space("Clone")
    await rig.runtime.on_user_message(space["id"], "clone https://www.igloo.inc/")
    await wait_run_done(rig.runtime, space["id"])
    answer = [m for m in rig.store.list_messages(space["id"]) if m["role"] == "assistant"][-1]
    assert "analog_clone" in answer["content"]
    html = rig.settings.data_dir / "spaces" / space["id"] / "ws" / "index.html"
    assert html.is_file()
    body = html.read_text(encoding="utf-8")
    assert "Analog of www.igloo.inc" in body
    assert "gsap" in body.lower()


@pytest.mark.asyncio
async def test_compact_archives_old_turns(rig) -> None:
    space = rig.store.create_space("Long")
    for i in range(12):
        rig.store.add_message(space["id"], "user", f"turn-{i}")
        rig.store.add_message(space["id"], "assistant", f"ack-{i}")
    rig.llm.manager.extend(
        [
            LLMResult(tool_calls=[_tc("compact_conversation")]),
            LLMResult(text="Older turns archived. Working from the tail."),
        ]
    )
    await rig.runtime.on_user_message(space["id"], "compact this thread")
    await wait_run_done(rig.runtime, space["id"])
    tools = [m for m in rig.store.list_messages(space["id"]) if m["role"] == "tool"]
    compact = [m for m in tools if (m.get("meta") or {}).get("tool") == "compact_conversation"]
    assert compact and "compacted" in compact[0]["content"]
    space_row = rig.store.get_space(space["id"])
    assert space_row is not None and int(space_row.get("compact_seq") or 0) > 0
    latest = (rig.settings.data_dir / "spaces" / space["id"] / "ws" / "archives" / "LATEST.txt")
    assert latest.is_file()
    answer = [m for m in rig.store.list_messages(space["id"]) if m["role"] == "assistant"][-1]
    assert "archived" in answer["content"].lower() or "tail" in answer["content"].lower()
