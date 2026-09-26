"""EPIC-GRANT-02 (#203): the workspace jail widens only into Allow-ed session grants.

Every case drives the real tool path (``ws_ls`` / ``ws_read`` / ``ws_glob`` /
``ws_write`` through ``CrewRuntime._execute_tool``) and asserts on the tool
message the operator sees in the transcript, then the security cases are
repeated on ``SpaceWorkspace`` directly so the reason strings are pinned. The
grant session is the space id (what the UI records grants under).
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from CortexOS.crew import policy
from CortexOS.crew.llm import LLMResult, ToolCall
from CortexOS.crew.runtime import CrewRuntime
from CortexOS.crew.session_grants import (
    DESTINATION_LOCAL,
    GrantRefused,
    SessionGrantBook,
)
from CortexOS.crew.workspace import WorkspaceError, workspace_for
from tests.test_crew.conftest import FakeLLM, FakeMCPClient, wait_run_done


def _tc(tool: str, **args: object) -> ToolCall:
    return ToolCall(id=f"c-{tool}", name=tool, args=dict(args))


def _tool_messages(rig: Any, space_id: str) -> list[str]:
    return [
        str(m.get("content") or "")
        for m in rig.store.list_messages(space_id)
        if m.get("role") == "tool"
    ]


async def _run_tool(rig: Any, space_id: str, call: ToolCall) -> str:
    """Drive one manager tool call through the runtime; return its tool message."""
    before = len(_tool_messages(rig, space_id))
    rig.llm.manager.append(LLMResult(tool_calls=[call]))
    rig.llm.manager.append(LLMResult(text="done"))
    await rig.runtime.on_user_message(space_id, f"run {call.name}")
    await wait_run_done(rig.runtime, space_id)
    msgs = _tool_messages(rig, space_id)
    assert len(msgs) > before, "tool call never reached the transcript"
    return msgs[before]


def _make_symlink(link: Path, target: Path) -> None:
    try:
        os.symlink(str(target), str(link))
    except (OSError, NotImplementedError) as exc:  # pragma: no cover - platform gap
        pytest.skip(
            f"SYMLINK ESCAPE NOT VERIFIED ON THIS OS: cannot create a symlink ({exc}). "
            "The jail still resolves real paths, but this run did not prove it."
        )


@pytest.fixture()
def laptop(tmp_path: Path) -> dict[str, Path]:
    """A fake laptop: a folder the founder will Allow, a look-alike, an outsider."""
    work = tmp_path / "laptop" / "work"
    evil = tmp_path / "laptop" / "work-evil"
    outside = tmp_path / "laptop" / "outside"
    for d in (work / "sub", evil, outside):
        d.mkdir(parents=True)
    (work / "q3.xlsx").write_text("granted sheet", encoding="utf-8")
    (work / "sub" / "notes.md").write_text("granted notes", encoding="utf-8")
    (evil / "trap.txt").write_text("prefix trap", encoding="utf-8")
    (outside / "secret.txt").write_text("ungranted secret", encoding="utf-8")
    return {"work": work, "evil": evil, "outside": outside, "root": tmp_path}


# ---- tool path: inside the space ----------------------------------------------


@pytest.mark.asyncio
async def test_relative_paths_stay_inside_the_space_with_a_book_attached(rig, laptop) -> None:
    space = rig.store.create_space("HQ")
    rig.runtime.session_grants.record(space["id"], kind="folder", path=str(laptop["work"]))
    out = await _run_tool(rig, space["id"], _tc("ws_write", path="notes.md", content="hello"))
    assert "wrote notes.md" in out
    out = await _run_tool(rig, space["id"], _tc("ws_read", path="notes.md"))
    assert "hello" in out
    out = await _run_tool(rig, space["id"], _tc("ws_ls", path="."))
    assert "notes.md" in out
    out = await _run_tool(rig, space["id"], _tc("ws_read", path="../secrets.txt"))
    assert out.startswith("DENIED") and "escape" in out


# ---- tool path: outside the space ---------------------------------------------


@pytest.mark.asyncio
async def test_ungranted_absolute_path_is_refused_with_the_reason(rig, laptop) -> None:
    space = rig.store.create_space("HQ")
    secret = laptop["outside"] / "secret.txt"
    out = await _run_tool(rig, space["id"], _tc("ws_read", path=str(secret)))
    assert out.startswith("DENIED")
    assert "R-0011" in out
    assert "no folder grant with decision allow" in out
    assert "ungranted secret" not in out
    out = await _run_tool(rig, space["id"], _tc("ws_ls", path=str(laptop["outside"])))
    assert out.startswith("DENIED") and "R-0011" in out and "secret.txt" not in out
    out = await _run_tool(rig, space["id"], _tc("ws_glob", pattern=str(laptop["outside"] / "*")))
    assert out.startswith("DENIED") and "R-0011" in out and "secret.txt" not in out


@pytest.mark.asyncio
async def test_allowed_folder_grant_admits_ls_read_glob(rig, laptop) -> None:
    space = rig.store.create_space("HQ")
    row = rig.runtime.session_grants.record(space["id"], kind="folder", path=str(laptop["work"]))
    assert row["decision"] == "allow"
    out = await _run_tool(rig, space["id"], _tc("ws_ls", path=str(laptop["work"])))
    assert "q3.xlsx" in out and "sub" in out
    out = await _run_tool(rig, space["id"], _tc("ws_read", path=str(laptop["work"] / "q3.xlsx")))
    assert "granted sheet" in out
    out = await _run_tool(
        rig, space["id"], _tc("ws_read", path=str(laptop["work"] / "sub" / "notes.md"))
    )
    assert "granted notes" in out
    out = await _run_tool(rig, space["id"], _tc("ws_glob", pattern=str(laptop["work"] / "*.md")))
    assert str(laptop["work"] / "sub" / "notes.md") in out
    assert "trap.txt" not in out and "secret.txt" not in out
    # the grant does not leak sideways: the outsider is still closed
    out = await _run_tool(
        rig, space["id"], _tc("ws_read", path=str(laptop["outside"] / "secret.txt"))
    )
    assert out.startswith("DENIED") and "R-0011" in out


@pytest.mark.asyncio
async def test_prefix_trap_is_refused(rig, laptop) -> None:
    space = rig.store.create_space("HQ")
    rig.runtime.session_grants.record(space["id"], kind="folder", path=str(laptop["work"]))
    out = await _run_tool(rig, space["id"], _tc("ws_read", path=str(laptop["evil"] / "trap.txt")))
    assert out.startswith("DENIED") and "R-0011" in out and "prefix trap" not in out
    out = await _run_tool(rig, space["id"], _tc("ws_ls", path=str(laptop["evil"])))
    assert out.startswith("DENIED") and "trap.txt" not in out


@pytest.mark.asyncio
async def test_dotdot_escape_from_a_granted_folder_is_refused(rig, laptop) -> None:
    space = rig.store.create_space("HQ")
    rig.runtime.session_grants.record(space["id"], kind="folder", path=str(laptop["work"]))
    sneaky = str(laptop["work"]) + os.sep + ".." + os.sep + "outside" + os.sep + "secret.txt"
    out = await _run_tool(rig, space["id"], _tc("ws_read", path=sneaky))
    assert out.startswith("DENIED") and "R-0011" in out and "'..'" in out
    assert "ungranted secret" not in out


@pytest.mark.asyncio
async def test_symlink_escape_from_a_granted_folder_is_refused(rig, laptop) -> None:
    space = rig.store.create_space("HQ")
    rig.runtime.session_grants.record(space["id"], kind="folder", path=str(laptop["work"]))
    _make_symlink(laptop["work"] / "link.txt", laptop["outside"] / "secret.txt")
    _make_symlink(laptop["work"] / "linkdir", laptop["outside"])
    out = await _run_tool(rig, space["id"], _tc("ws_read", path=str(laptop["work"] / "link.txt")))
    assert out.startswith("DENIED") and "R-0011" in out and "ungranted secret" not in out
    out = await _run_tool(
        rig, space["id"], _tc("ws_read", path=str(laptop["work"] / "linkdir" / "secret.txt"))
    )
    assert out.startswith("DENIED") and "R-0011" in out and "ungranted secret" not in out
    out = await _run_tool(rig, space["id"], _tc("ws_glob", pattern=str(laptop["work"] / "*")))
    assert "q3.xlsx" in out
    assert "link.txt" not in out and "secret.txt" not in out


@pytest.mark.asyncio
async def test_cancelled_grant_admits_nothing(rig, laptop) -> None:
    space = rig.store.create_space("HQ")
    rig.runtime.session_grants.record(
        space["id"], kind="folder", path=str(laptop["work"]), decision="cancel"
    )
    out = await _run_tool(rig, space["id"], _tc("ws_read", path=str(laptop["work"] / "q3.xlsx")))
    assert out.startswith("DENIED") and "R-0011" in out and "granted sheet" not in out
    # a later Cancel on an Allow-ed folder closes it again (upsert by identity)
    rig.runtime.session_grants.record(space["id"], kind="folder", path=str(laptop["work"]))
    out = await _run_tool(rig, space["id"], _tc("ws_read", path=str(laptop["work"] / "q3.xlsx")))
    assert "granted sheet" in out
    rig.runtime.session_grants.record(
        space["id"], kind="folder", path=str(laptop["work"]), decision="cancel"
    )
    out = await _run_tool(rig, space["id"], _tc("ws_read", path=str(laptop["work"] / "q3.xlsx")))
    assert out.startswith("DENIED") and "granted sheet" not in out


@pytest.mark.asyncio
async def test_another_sessions_grant_is_refused(rig, laptop) -> None:
    mine = rig.store.create_space("HQ")
    theirs = rig.store.create_space("Other")
    rig.runtime.session_grants.record(theirs["id"], kind="folder", path=str(laptop["work"]))
    out = await _run_tool(rig, mine["id"], _tc("ws_read", path=str(laptop["work"] / "q3.xlsx")))
    assert out.startswith("DENIED") and "R-0011" in out and "granted sheet" not in out
    out = await _run_tool(rig, theirs["id"], _tc("ws_read", path=str(laptop["work"] / "q3.xlsx")))
    assert "granted sheet" in out


@pytest.mark.asyncio
async def test_write_and_edit_outside_the_space_stay_refused_even_with_a_grant(rig, laptop) -> None:
    space = rig.store.create_space("HQ")
    rig.runtime.session_grants.record(space["id"], kind="folder", path=str(laptop["work"]))
    target = laptop["work"] / "q3.xlsx"
    out = await _run_tool(rig, space["id"], _tc("ws_write", path=str(target), content="x"))
    assert out.startswith("DENIED") and "R-0011" in out and "reads only" in out
    out = await _run_tool(
        rig, space["id"], _tc("ws_edit", path=str(target), old="granted", new="edited")
    )
    assert out.startswith("DENIED") and "R-0011" in out and "reads only" in out
    assert target.read_text(encoding="utf-8") == "granted sheet"
    assert not (laptop["work"] / "new.txt").exists()
    out = await _run_tool(
        rig, space["id"], _tc("ws_write", path=str(laptop["work"] / "new.txt"), content="x")
    )
    assert out.startswith("DENIED")
    assert not (laptop["work"] / "new.txt").exists()


@pytest.mark.asyncio
async def test_profile_root_and_drive_root_are_refused_and_say_why(rig, laptop, settings) -> None:
    home = laptop["root"] / "home" / "ops"
    (home / "AppData").mkdir(parents=True)
    (home / "AppData" / "cookie.txt").write_text("appdata", encoding="utf-8")
    rig.runtime.session_grants = SessionGrantBook(profile_roots=[str(home)])
    space = rig.store.create_space("HQ")
    out = await _run_tool(rig, space["id"], _tc("ws_ls", path=str(home)))
    assert out.startswith("DENIED") and "R-0011" in out
    assert "user profile root is not a grantable folder" in out
    assert "cookie" not in out
    # ungranted AppData under the profile: refused, names the missing grant
    out = await _run_tool(
        rig, space["id"], _tc("ws_read", path=str(home / "AppData" / "cookie.txt"))
    )
    assert out.startswith("DENIED") and "no folder grant with decision allow" in out
    assert "appdata" not in out
    # a drive root can never be a grant: the book refuses it before any lookup
    with pytest.raises(GrantRefused, match="drive root"):
        rig.runtime.session_grants.granted_folder_for(space["id"], "C:\\")
    with pytest.raises(GrantRefused, match="filesystem root"):
        rig.runtime.session_grants.granted_folder_for(space["id"], "/")
    out = await _run_tool(rig, space["id"], _tc("ws_ls", path="C:\\"))
    assert out.startswith("DENIED") and "R-0011" in out
    out = await _run_tool(rig, space["id"], _tc("ws_ls", path="/"))
    assert out.startswith("DENIED") and "R-0011" in out


# ---- a grant is not an approval -------------------------------------------------


@pytest.mark.asyncio
async def test_click_with_master_off_stays_denied_despite_a_grant(rig, laptop) -> None:
    assert rig.settings.master_computer_control is False
    fake = FakeMCPClient("uacc", [{"name": "click", "inputSchema": {"type": "object"}}])
    rig.mcp.clients["uacc"] = fake
    space = rig.store.create_space("Desk")
    rig.runtime.session_grants.record(space["id"], kind="folder", path=str(laptop["work"]))
    rig.llm.manager.extend(
        [LLMResult(tool_calls=[_tc("mcp_uacc_click", x=1, y=2)]), LLMResult(text="stopped")]
    )
    await rig.runtime.on_user_message(space["id"], "click it")
    await wait_run_done(rig.runtime, space["id"])
    assert fake.called == []
    assert rig.mcp.master_on is False
    assert rig.store.pending_confirms(space["id"]) == []
    painted = " ".join(_tool_messages(rig, space["id"]) + ["", ""])
    answer = [m for m in rig.store.list_messages(space["id"]) if m["role"] == "assistant"][-1]
    assert "computer control is off" in (painted + answer["content"]).lower()


@pytest.mark.asyncio
async def test_mutating_click_still_confirms_with_a_grant_present(
    settings, crew_env, laptop
) -> None:
    from CortexOS.crew.events import EventBus
    from CortexOS.crew.mcp_client import MCPManager
    from CortexOS.crew.store import CrewStore
    from tests.test_crew.conftest import FakeBridge

    settings.master_computer_control = True
    store = CrewStore(settings.db_path)
    bus = EventBus()
    mcp = MCPManager(settings.mcp_config_path, master_on=True)
    fake = FakeMCPClient(
        "uacc",
        [
            {"name": "click", "inputSchema": {"type": "object"}},
            {"name": "type_text", "inputSchema": {"type": "object"}},
        ],
        armed=True,
    )
    mcp.clients["uacc"] = fake
    llm = FakeLLM()
    book = SessionGrantBook()
    runtime = CrewRuntime(
        store, bus, settings, mcp, FakeBridge(), llm_chat=llm, session_grants=book
    )
    space = store.create_space("Desk")
    book.record(space["id"], kind="folder", path=str(laptop["work"]))
    book.record(space["id"], kind="window", title="Book1 - Excel", pid=4242)
    llm.manager.extend(
        [
            LLMResult(tool_calls=[_tc("mcp_uacc_click", x=1, y=2)]),
            LLMResult(tool_calls=[_tc("mcp_uacc_type_text", text="hello")]),
            LLMResult(text="Operator kept the mouse."),
        ]
    )
    seen: list[str] = []

    async def auto_deny() -> None:
        for _ in range(400):
            pending = store.pending_confirms(space["id"])
            if pending:
                seen.append(str(pending[0]["tool"]))
                runtime.decide_confirm(pending[0]["id"], False)
                if len(seen) == 2:
                    return
            await asyncio.sleep(0.01)
        raise AssertionError(f"confirm never appeared for both tools: {seen}")

    waiter = asyncio.create_task(auto_deny())
    await runtime.on_user_message(space["id"], "click and type")
    await wait_run_done(runtime, space["id"])
    await waiter
    assert seen == ["uacc.click", "uacc.type_text"]
    assert fake.called == []
    assert policy.decide("click", server="uacc", armed=True, master_on=True) == (
        policy.CONFIRM,
        "mutating computer-control tool needs operator approval",
    )
    store.close()


def test_policy_grant_reach_classification() -> None:
    assert policy.grant_reach("ws_ls") == policy.REACH_READ
    assert policy.grant_reach("ws_read") == policy.REACH_READ
    assert policy.grant_reach("ws_glob") == policy.REACH_READ
    assert policy.grant_reach("ws_write") == policy.REACH_WRITE
    assert policy.grant_reach("ws_edit") == policy.REACH_WRITE
    assert policy.grant_reach("click") == policy.REACH_NONE
    # the master switch is untouched by anything grant-shaped
    assert policy.decide("click", server="uacc", armed=True, master_on=False)[0] == policy.DENY


# ---- destination on the grant row ---------------------------------------------


def test_grant_rows_say_destination_local(settings, crew_env, tmp_path: Path) -> None:
    from CortexOS.crew.server import create_app

    app = create_app(settings, llm_chat=FakeLLM())
    with TestClient(app) as tc:
        resp = tc.post(
            "/crew/session-grants",
            json={"session_id": "s1", "kind": "folder", "path": str(tmp_path / "docs")},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["grant"]["destination"] == DESTINATION_LOCAL == "local"
        resp = tc.post(
            "/crew/session-grants",
            json={"session_id": "s1", "kind": "window", "title": "Book1", "pid": 7},
        )
        assert resp.status_code == 200, resp.text
        rows = tc.get("/crew/session-grants", params={"session_id": "s1"}).json()["grants"]
        assert [r["destination"] for r in rows] == ["local", "local"]
    # a stale persisted row without the field, or with a foreign value, reads local
    stale = tmp_path / "stale.json"
    stale.write_text(
        json.dumps(
            {
                "sessions": {
                    "s9": [
                        {
                            "id": "a",
                            "kind": "folder",
                            "decision": "allow",
                            "persist": True,
                            "path": str(tmp_path / "docs"),
                            "identity": "folder:x",
                        },
                        {
                            "id": "b",
                            "kind": "folder",
                            "decision": "allow",
                            "persist": True,
                            "path": str(tmp_path / "docs2"),
                            "identity": "folder:y",
                            "destination": "cloud-worker",
                        },
                    ]
                }
            }
        ),
        encoding="utf-8",
    )
    rows = SessionGrantBook(stale).grants_for("s9")
    assert [r["destination"] for r in rows] == ["local", "local"]


# ---- workspace unit: pinned reasons --------------------------------------------


def test_workspace_without_a_book_refuses_every_absolute_path(tmp_path: Path, laptop) -> None:
    ws = workspace_for(tmp_path / "crew", "space-1")
    with pytest.raises(WorkspaceError, match="R-0011.*no session grant book"):
        ws.read(str(laptop["work"] / "q3.xlsx"))
    with pytest.raises(WorkspaceError, match="absolute"):
        ws.write("C:/Windows/x.txt", "no")


def test_workspace_grant_lookup_is_component_wise_and_names_the_grant(
    tmp_path: Path, laptop
) -> None:
    book = SessionGrantBook()
    book.record("sess-1", kind="folder", path=str(laptop["work"]))
    ws = workspace_for(tmp_path / "crew", "space-1", grants=book, session_id="sess-1")
    assert ws.resolve(str(laptop["work"])) == laptop["work"].resolve()
    assert ws.resolve(str(laptop["work"] / "sub")) == (laptop["work"] / "sub").resolve()
    with pytest.raises(WorkspaceError, match="R-0011.*no folder grant with decision allow"):
        ws.resolve(str(laptop["evil"] / "trap.txt"))
    with pytest.raises(WorkspaceError, match=r"R-0011.*'\.\.' traversal"):
        ws.resolve(str(laptop["work"]) + "/../outside/secret.txt")
    with pytest.raises(WorkspaceError, match="reads only"):
        ws.resolve(str(laptop["work"] / "x.txt"), reach=policy.REACH_WRITE)
    assert book.is_path_granted("sess-1", str(laptop["work"] / "q3.xlsx")) is True
    assert book.is_path_granted("sess-1", str(laptop["evil"] / "trap.txt")) is False
    assert book.is_path_granted("sess-2", str(laptop["work"] / "q3.xlsx")) is False
    assert book.is_path_granted("sess-1", "/") is False
    assert book.granted_folder_for("sess-1", str(laptop["work"] / "sub" / "notes.md")) == str(
        laptop["work"]
    )


def test_windows_style_grant_is_case_insensitive_and_prefix_safe() -> None:
    book = SessionGrantBook(profile_roots=[r"C:\Users\ops"])
    book.record("s1", kind="folder", path=r"D:\work")
    assert book.granted_folder_for("s1", r"d:\WORK\q3.xlsx") == r"D:\work"
    assert book.granted_folder_for("s1", r"D:\work") == r"D:\work"
    assert book.granted_folder_for("s1", r"D:\work-evil\x.txt") is None
    assert book.granted_folder_for("s1", r"D:\workspace") is None
    assert book.granted_folder_for("s1", r"C:\Users\ops\AppData") is None
    with pytest.raises(GrantRefused, match="profile root"):
        book.granted_folder_for("s1", r"C:\Users\ops")
    with pytest.raises(GrantRefused, match="drive root"):
        book.granted_folder_for("s1", r"C:\\")
    with pytest.raises(GrantRefused, match="'..'"):
        book.granted_folder_for("s1", r"D:\work\..\x")
