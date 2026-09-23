"""EPIC-GRANT-04 (#205): granted reads skip the per-read confirm; open windows
attach and are never launched; Excel is read as data; History is refused.

Every case drives the real tool path through ``CrewRuntime._execute_tool`` and
asserts on the tool message the operator sees in the transcript. The grant
session is the space id, as EPIC-GRANT-02 fixed it.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from CortexOS.crew import granted_reach, policy
from CortexOS.crew.events import EventBus
from CortexOS.crew.llm import LLMResult, ToolCall
from CortexOS.crew.mcp_client import MCPManager
from CortexOS.crew.runtime import CrewRuntime
from CortexOS.crew.session_grants import SessionGrantBook
from CortexOS.crew.store import CrewStore
from CortexOS.crew.workspace import WorkspaceError, workspace_for
from tests.test_crew.conftest import FakeBridge, FakeLLM, FakeMCPClient, wait_run_done

# Any tool whose name reads as launching, opening or starting something. The
# attach path must never send one of these to the MCP client (R-0015).
LAUNCH_LIKE = ("launch", "open", "start", "run", "exec", "spawn", "activate", "focus")


def _tc(tool: str, **args: object) -> ToolCall:
    return ToolCall(id=f"c-{tool}", name=tool, args=dict(args))


def _tool_messages(rig: Any, space_id: str) -> list[str]:
    return [
        str(m.get("content") or "")
        for m in rig.store.list_messages(space_id)
        if m.get("role") == "tool"
    ]


async def _run_tool(rig: Any, space_id: str, call: ToolCall) -> str:
    before = len(_tool_messages(rig, space_id))
    rig.llm.manager.append(LLMResult(tool_calls=[call]))
    rig.llm.manager.append(LLMResult(text="done"))
    await rig.runtime.on_user_message(space_id, f"run {call.name}")
    await wait_run_done(rig.runtime, space_id)
    msgs = _tool_messages(rig, space_id)
    assert len(msgs) > before, "tool call never reached the transcript"
    return msgs[before]


def _forbid_confirm(rig: Any) -> None:
    """Make any walk of the CONFIRM ladder a loud failure for this rig."""

    async def boom(*_a: object, **_k: object) -> str:
        raise AssertionError("a per-read confirm was requested on a granted read")

    rig.runtime._await_confirm = boom  # type: ignore[method-assign]


class WindowsClient(FakeMCPClient):
    """UACC double: answers ``list_windows`` with a fixed live set and also
    advertises launch-like tools so a wrong call would be visible."""

    def __init__(self, windows: list[dict[str, Any]], *, armed: bool = True, text: str | None = None) -> None:
        names = (
            "list_windows",
            "click",
            "type_text",
            "launch_app",
            "open_application",
            "start_process",
            "activate_window",
        )
        super().__init__(
            "uacc", [{"name": n, "inputSchema": {"type": "object"}} for n in names], armed=armed
        )
        self.windows = windows
        self.text = text

    async def call(self, tool: str, args: dict[str, Any], timeout: int = 90) -> str:
        out = await super().call(tool, args, timeout)
        if tool == "list_windows":
            return self.text if self.text is not None else json.dumps({"windows": self.windows})
        return out


def _armed_rig(settings: Any, client: FakeMCPClient) -> SimpleNamespace:
    settings.master_computer_control = True
    store = CrewStore(settings.db_path)
    bus = EventBus()
    mcp = MCPManager(settings.mcp_config_path, master_on=True)
    mcp.clients["uacc"] = client
    llm = FakeLLM()
    book = SessionGrantBook()
    runtime = CrewRuntime(store, bus, settings, mcp, FakeBridge(), llm_chat=llm, session_grants=book)
    return SimpleNamespace(
        store=store, bus=bus, mcp=mcp, llm=llm, runtime=runtime, settings=settings, client=client
    )


def _assert_no_launch(client: FakeMCPClient) -> None:
    names = [name for name, _ in client.called]
    offenders = [n for n in names if any(word in n.lower() for word in LAUNCH_LIKE)]
    assert offenders == [], f"attach path sent a launch-like tool: {offenders}"
    assert set(names) <= {granted_reach.ATTACH_TOOL}, names


@pytest.fixture()
def laptop(tmp_path: Path) -> dict[str, Path]:
    work = tmp_path / "laptop" / "work"
    outside = tmp_path / "laptop" / "outside"
    for d in (work, outside):
        d.mkdir(parents=True)
    (work / "notes.md").write_text("granted notes", encoding="utf-8")
    (outside / "secret.txt").write_text("ungranted secret", encoding="utf-8")
    return {"work": work, "outside": outside, "root": tmp_path}


def _make_workbook(path: Path) -> None:
    import openpyxl

    book = openpyxl.Workbook()
    sheet = book.active
    sheet.title = "Q3"
    sheet.append(["sku", "qty", "price"])
    sheet.append(["SKU-ALPHA", 12, 3.5])
    sheet.append(["SKU-BETA", 7, None])
    second = book.create_sheet("Notes")
    second.append(["note", "second sheet cell"])
    book.save(str(path))


# ---- 1. granted reads take no per-read confirm ----------------------------------


@pytest.mark.asyncio
async def test_granted_read_runs_without_a_per_read_confirm(rig, laptop) -> None:
    _forbid_confirm(rig)
    space = rig.store.create_space("HQ")
    rig.runtime.session_grants.record(space["id"], kind="folder", path=str(laptop["work"]))
    out = await _run_tool(rig, space["id"], _tc("ws_read", path=str(laptop["work"] / "notes.md")))
    assert "granted notes" in out
    out = await _run_tool(rig, space["id"], _tc("ws_ls", path=str(laptop["work"])))
    assert "notes.md" in out
    out = await _run_tool(rig, space["id"], _tc("ws_glob", pattern=str(laptop["work"] / "*.md")))
    assert "notes.md" in out
    assert rig.store.pending_confirms(space["id"]) == []


@pytest.mark.asyncio
async def test_ungranted_read_is_still_refused_and_never_asks(rig, laptop) -> None:
    _forbid_confirm(rig)
    space = rig.store.create_space("HQ")
    rig.runtime.session_grants.record(space["id"], kind="folder", path=str(laptop["work"]))
    secret = laptop["outside"] / "secret.txt"
    out = await _run_tool(rig, space["id"], _tc("ws_read", path=str(secret)))
    assert out.startswith("DENIED") and "R-0011" in out and "ungranted secret" not in out
    assert rig.store.pending_confirms(space["id"]) == []


# ---- 2. window attach: verify only, never launch --------------------------------


@pytest.mark.asyncio
async def test_attach_succeeds_for_a_granted_live_window_and_click_still_confirms(
    settings, crew_env
) -> None:
    client = WindowsClient(
        [
            {"pid": 4242, "title": "Budget - OneNote", "hwnd": 991},
            {"pid": 77, "title": "Downloads - File Explorer", "hwnd": 5},
        ]
    )
    r = _armed_rig(settings, client)
    space = r.store.create_space("Desk")
    r.runtime.session_grants.record(space["id"], kind="window", title="Budget - OneNote", pid=4242)
    r.llm.manager.extend(
        [
            LLMResult(tool_calls=[_tc("attach_window", title="budget  -  onenote", pid=4242)]),
            LLMResult(tool_calls=[_tc("mcp_uacc_click", x=1, y=2)]),
            LLMResult(tool_calls=[_tc("mcp_uacc_type_text", text="hello")]),
            LLMResult(text="kept the mouse"),
        ]
    )
    seen: list[str] = []

    async def auto_deny() -> None:
        for _ in range(600):
            pending = r.store.pending_confirms(space["id"])
            if pending:
                seen.append(str(pending[0]["tool"]))
                r.runtime.decide_confirm(pending[0]["id"], False)
                if len(seen) == 2:
                    return
            await asyncio.sleep(0.01)
        raise AssertionError(f"confirm never appeared for both tools: {seen}")

    waiter = asyncio.create_task(auto_deny())
    await r.runtime.on_user_message(space["id"], "attach then click")
    await wait_run_done(r.runtime, space["id"])
    await waiter
    msgs = _tool_messages(r, space["id"])
    attached = [m for m in msgs if m.startswith("ATTACHED ")]
    assert len(attached) == 1, msgs
    payload = json.loads(attached[0][len("ATTACHED ") :])
    assert payload == {
        "attached": True,
        "pid": 4242,
        "title": "Budget - OneNote",
        "handle": 991,
        "grant_id": payload["grant_id"],
        "via": "uacc.list_windows",
        "launched": False,
    }
    # the attach was the only MCP call; the click and the type were confirmed and denied
    assert client.called == [("list_windows", {})]
    _assert_no_launch(client)
    assert seen == ["uacc.click", "uacc.type_text"]
    assert r.store.pending_confirms(space["id"]) == []
    r.store.close()


@pytest.mark.asyncio
async def test_attach_without_a_window_grant_is_refused_before_any_mcp_call(
    settings, crew_env
) -> None:
    client = WindowsClient([{"pid": 4242, "title": "Budget - OneNote"}])
    r = _armed_rig(settings, client)
    space = r.store.create_space("Desk")
    out = await _run_tool(r, space["id"], _tc("attach_window", title="Budget - OneNote", pid=4242))
    assert out.startswith("DENIED") and "R-0015" in out and "missing grant: window allow" in out
    assert "never launches" in out
    assert client.called == []
    # another session's grant opens nothing
    other = r.store.create_space("Other")
    r.runtime.session_grants.record(other["id"], kind="window", title="Budget - OneNote", pid=4242)
    out = await _run_tool(r, space["id"], _tc("attach_window", title="Budget - OneNote", pid=4242))
    assert out.startswith("DENIED") and "R-0015" in out
    assert client.called == []
    r.store.close()


@pytest.mark.asyncio
async def test_attach_with_a_stale_pid_is_refused_and_nothing_is_launched(
    settings, crew_env
) -> None:
    # the grant says pid 4242; the live list shows the same title under a new pid
    client = WindowsClient([{"pid": 5151, "title": "Budget - OneNote"}])
    r = _armed_rig(settings, client)
    space = r.store.create_space("Desk")
    r.runtime.session_grants.record(space["id"], kind="window", title="Budget - OneNote", pid=4242)
    out = await _run_tool(r, space["id"], _tc("attach_window", title="Budget - OneNote", pid=4242))
    assert out.startswith("DENIED") and "R-0015" in out and "not open now" in out
    assert "does not launch" in out
    assert client.called == [("list_windows", {})]
    _assert_no_launch(client)
    r.store.close()


@pytest.mark.asyncio
async def test_attach_with_a_renamed_title_is_refused(settings, crew_env) -> None:
    client = WindowsClient([{"pid": 4242, "title": "Budget.docx - Word"}])
    r = _armed_rig(settings, client)
    space = r.store.create_space("Desk")
    r.runtime.session_grants.record(space["id"], kind="window", title="Budget - Word", pid=4242)
    out = await _run_tool(r, space["id"], _tc("attach_window", title="Budget - Word", pid=4242))
    assert out.startswith("DENIED") and "R-0015" in out and "not open now" in out
    assert client.called == [("list_windows", {})]
    _assert_no_launch(client)
    r.store.close()


@pytest.mark.asyncio
async def test_attach_reads_a_plain_text_list_windows_result(settings, crew_env) -> None:
    text = "hwnd=12 pid=4242 title=Budget - OneNote\nhwnd=13 pid=99 title=Other"
    client = WindowsClient([], text=text)
    r = _armed_rig(settings, client)
    space = r.store.create_space("Desk")
    r.runtime.session_grants.record(space["id"], kind="window", title="Budget - OneNote", pid=4242)
    out = await _run_tool(r, space["id"], _tc("attach_window", title="Budget - OneNote", pid=4242))
    assert out.startswith("ATTACHED ")
    assert json.loads(out[len("ATTACHED ") :])["pid"] == 4242
    # a pid that only appears as part of a longer number is not that pid
    client.text = "hwnd=12 pid=14242 title=Budget - OneNote"
    out = await _run_tool(r, space["id"], _tc("attach_window", title="Budget - OneNote", pid=4242))
    assert out.startswith("DENIED") and "not open now" in out
    _assert_no_launch(client)
    r.store.close()


@pytest.mark.asyncio
async def test_attach_with_master_off_is_refused_and_never_enables(rig) -> None:
    assert rig.settings.master_computer_control is False
    client = WindowsClient([{"pid": 4242, "title": "Budget - OneNote"}])
    rig.mcp.clients["uacc"] = client
    space = rig.store.create_space("Desk")
    rig.runtime.session_grants.record(space["id"], kind="window", title="Budget - OneNote", pid=4242)
    out = await _run_tool(rig, space["id"], _tc("attach_window", title="Budget - OneNote", pid=4242))
    assert out.startswith("DENIED") and "R-0015" in out
    assert "computer control is off" in out and "nothing was launched" in out
    assert client.called == []
    assert rig.mcp.master_on is False
    assert client.spec.armed is True  # unchanged; nothing was toggled either way


@pytest.mark.asyncio
async def test_attach_with_uacc_disarmed_or_absent_is_refused_and_never_arms(
    settings, crew_env
) -> None:
    client = WindowsClient([{"pid": 4242, "title": "Budget - OneNote"}], armed=False)
    r = _armed_rig(settings, client)
    space = r.store.create_space("Desk")
    r.runtime.session_grants.record(space["id"], kind="window", title="Budget - OneNote", pid=4242)
    out = await _run_tool(r, space["id"], _tc("attach_window", title="Budget - OneNote", pid=4242))
    assert out.startswith("DENIED") and "R-0015" in out and "not armed" in out
    assert client.called == [] and client.spec.armed is False and client.starts == 0
    r.mcp.clients.pop("uacc")
    out = await _run_tool(r, space["id"], _tc("attach_window", title="Budget - OneNote", pid=4242))
    assert out.startswith("DENIED") and "not catalogued" in out and "nothing was launched" in out
    r.store.close()


def test_attach_tool_is_pinned_to_list_windows_and_title_rule_is_exact() -> None:
    assert granted_reach.ATTACH_TOOL == "list_windows"
    assert granted_reach.ATTACH_TOOL in policy.READ_ONLY_TOOLS
    assert not any(word in granted_reach.ATTACH_TOOL for word in LAUNCH_LIKE)
    assert granted_reach.titles_match("Budget - OneNote", "  budget -   ONENOTE ")
    assert not granted_reach.titles_match("Budget - OneNote", "Budget - OneNote (2)")
    assert not granted_reach.titles_match("", "")
    rows = granted_reach.parse_windows(json.dumps([{"process_id": "7", "name": "A", "handle": 1}]))
    assert rows[0]["pid"] == 7 and rows[0]["title"] == "A" and rows[0]["handle"] == 1
    assert granted_reach.find_live_window(rows, "a", 7) is rows[0]
    assert granted_reach.find_live_window(rows, "a", 8) is None
    assert granted_reach.parse_windows("") == []


# ---- 3. Excel as data ----------------------------------------------------------


@pytest.mark.asyncio
async def test_granted_xlsx_is_read_as_data_through_openpyxl(rig, laptop) -> None:
    _forbid_confirm(rig)
    book_path = laptop["work"] / "q3.xlsx"
    _make_workbook(book_path)
    space = rig.store.create_space("HQ")
    rig.runtime.session_grants.record(space["id"], kind="folder", path=str(laptop["work"]))
    out = await _run_tool(rig, space["id"], _tc("ws_read_xlsx", path=str(book_path)))
    assert out.startswith("# ") and "sheet 'Q3'" in out and "openpyxl, not Excel" in out
    assert "sku\tqty\tprice" in out
    assert "SKU-ALPHA\t12\t3.5" in out
    assert "SKU-BETA\t7" in out
    assert "second sheet cell" not in out
    out = await _run_tool(
        rig, space["id"], _tc("ws_read_xlsx", path=str(book_path), sheet="Notes", limit=1)
    )
    assert "second sheet cell" in out and "SKU-ALPHA" not in out
    out = await _run_tool(rig, space["id"], _tc("ws_read_xlsx", path=str(book_path), sheet="Nope"))
    assert out.startswith("DENIED") and "sheet 'Nope' not in" in out
    assert rig.store.pending_confirms(space["id"]) == []
    # nothing on the MCP side was touched: no Excel window, no COM
    assert rig.mcp.clients.get("uacc") is None or rig.mcp.clients["uacc"].tools == []


@pytest.mark.asyncio
async def test_ungranted_xlsx_is_refused(rig, laptop) -> None:
    book_path = laptop["outside"] / "secret.xlsx"
    _make_workbook(book_path)
    space = rig.store.create_space("HQ")
    rig.runtime.session_grants.record(space["id"], kind="folder", path=str(laptop["work"]))
    out = await _run_tool(rig, space["id"], _tc("ws_read_xlsx", path=str(book_path)))
    assert out.startswith("DENIED") and "R-0011" in out and "SKU-ALPHA" not in out


def test_xlsx_reader_caps_size_and_refuses_other_extensions(
    tmp_path: Path, laptop, monkeypatch: pytest.MonkeyPatch
) -> None:
    book_path = laptop["work"] / "q3.xlsx"
    _make_workbook(book_path)
    book = SessionGrantBook()
    book.record("s1", kind="folder", path=str(laptop["work"]))
    ws = workspace_for(tmp_path / "crew", "s1", grants=book, session_id="s1")
    assert "SKU-ALPHA" in ws.read_xlsx(str(book_path))
    monkeypatch.setattr(granted_reach, "MAX_XLSX_BYTES", 16)
    with pytest.raises(WorkspaceError, match="exceeds 16 bytes"):
        ws.read_xlsx(str(book_path))
    monkeypatch.setattr(granted_reach, "MAX_XLSX_BYTES", 2 * 1024 * 1024)
    with pytest.raises(WorkspaceError, match="not a workbook this reader opens"):
        ws.read_xlsx(str(laptop["work"] / "notes.md"))
    fake = laptop["work"] / "broken.xlsx"
    fake.write_bytes(b"not a zip")
    with pytest.raises(WorkspaceError, match="cannot open"):
        ws.read_xlsx(str(fake))
    # inside the space folder a relative path works the same way
    _make_workbook(ws.root / "local.xlsx")
    assert "SKU-BETA" in ws.read_xlsx("local.xlsx")


# ---- 4. browser history is refused even inside a granted folder ------------------


@pytest.mark.asyncio
async def test_history_sqlite_is_refused_inside_a_granted_folder(rig, laptop) -> None:
    profile = laptop["work"] / "User Data" / "Default"
    profile.mkdir(parents=True)
    firefox = laptop["work"] / "profile.default"
    firefox.mkdir()
    names = {
        profile / "History": "VISITED-chrome-rows",
        profile / "History-journal": "VISITED-journal-rows",
        profile / "Archived History": "VISITED-old-rows",
        firefox / "places.sqlite": "VISITED-firefox-rows",
        firefox / "places.sqlite-wal": "VISITED-wal-rows",
    }
    for path, body in names.items():
        path.write_text(body, encoding="utf-8")
    (profile / "Bookmarks").write_text("bookmarks are not history", encoding="utf-8")
    space = rig.store.create_space("HQ")
    rig.runtime.session_grants.record(space["id"], kind="folder", path=str(laptop["work"]))
    for path, body in names.items():
        out = await _run_tool(rig, space["id"], _tc("ws_read", path=str(path)))
        assert out.startswith("DENIED"), (path, out)
        assert "R-0011" in out and "browser history database" in out
        assert "no such grant kind exists in v1" in out
        assert body not in out
        out = await _run_tool(rig, space["id"], _tc("ws_ls", path=str(path)))
        assert out.startswith("DENIED") and "browser history database" in out
    # the same folder grant still admits its ordinary files
    out = await _run_tool(rig, space["id"], _tc("ws_read", path=str(profile / "Bookmarks")))
    assert "bookmarks are not history" in out
    out = await _run_tool(rig, space["id"], _tc("ws_read", path=str(laptop["work"] / "notes.md")))
    assert "granted notes" in out


def test_history_refusal_follows_links_and_matches_names_only(tmp_path: Path, laptop) -> None:
    profile = laptop["work"] / "Default"
    profile.mkdir()
    (profile / "History").write_text("chrome visits", encoding="utf-8")
    (profile / "history.txt").write_text("a text file", encoding="utf-8")
    (profile / "MyHistory").write_text("not the browser", encoding="utf-8")
    book = SessionGrantBook()
    book.record("s1", kind="folder", path=str(laptop["work"]))
    ws = workspace_for(tmp_path / "crew", "s1", grants=book, session_id="s1")
    with pytest.raises(WorkspaceError, match="browser history database"):
        ws.read(str(profile / "History"))
    assert "a text file" in ws.read(str(profile / "history.txt"))
    assert "not the browser" in ws.read(str(profile / "MyHistory"))
    link = profile / "innocent.db"
    try:
        os.symlink(str(profile / "History"), str(link))
    except (OSError, NotImplementedError) as exc:  # pragma: no cover - platform gap
        pytest.skip(f"HISTORY LINK CASE NOT VERIFIED ON THIS OS: {exc}")
    with pytest.raises(WorkspaceError, match="browser history database"):
        ws.read(str(link))
    for name in ("History", "history", "HISTORY-wal", "Archived History", "places.sqlite-shm"):
        assert granted_reach.is_history_database(name), name
    for name in ("History.bak", "history.txt", "places.sqlite.old", "", "Bookmarks"):
        assert not granted_reach.is_history_database(name), name


# ---- policy classification ---------------------------------------------------------


def test_new_tools_are_crew_internal_reads_and_mutations_unchanged() -> None:
    assert "attach_window" in policy.INTERNAL_TOOLS
    assert "ws_read_xlsx" in policy.INTERNAL_TOOLS
    assert policy.grant_reach("ws_read_xlsx") == policy.REACH_READ
    assert policy.grant_reach("attach_window") == policy.REACH_NONE
    assert policy.decide("ws_read_xlsx", server=None, armed=False, master_on=False)[0] == policy.ALLOW
    assert policy.decide("attach_window", server=None, armed=False, master_on=False)[0] == policy.ALLOW
    # the mutating ladder is untouched
    assert policy.decide("click", server="uacc", armed=True, master_on=True) == (
        policy.CONFIRM,
        "mutating computer-control tool needs operator approval",
    )
    assert policy.decide("type_text", server="uacc", armed=True, master_on=True)[0] == policy.CONFIRM
    assert policy.decide("list_windows", server="uacc", armed=True, master_on=False)[0] == policy.DENY
    assert policy.decide("list_windows", server="uacc", armed=False, master_on=True)[0] == policy.DENY
