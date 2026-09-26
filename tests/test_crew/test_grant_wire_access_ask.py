"""GRANT-WIRE (#204, refs #205): a missing-grant read raises the Allow / Cancel ask.

GRANT-01..04 landed the store, the jail, the dialog and the smooth read, but
nothing called ``window.crewAskAccess``: a refused ``ws_*`` read painted an
error in the transcript and the founder never saw the ask. This file pins the
wire, on what the founder receives:

1. Runtime: a ``ws_ls`` / ``ws_read`` / ``ws_glob`` refused *only* because no
   folder grant covers the path emits exactly one ``access_ask`` event on the
   space bus, naming the folder to Allow (GRANT-01 normalised) and the space.
   A refusal that can never be granted (drive root, ``/``, the profile root,
   ``..``, a history database, a write, a relative escape) emits nothing. The
   tool text still carries the R-0011 reason either way.
2. Browser: the real channel end to end. A scripted manager issues the read
   over ``POST /crew/spaces/{id}/messages``, the runtime refuses it, the event
   travels the SSE stream into the served page and the GRANT-03 dialog opens
   listing the folder as text. Cancel POSTs ``decision=cancel``. A second
   refusal for the same folder while the ask is open opens no second dialog;
   after Cancel a fresh refusal asks again.

HT1 (founder walks Allow and Cancel on real Documents) stays a human gate.
"""

from __future__ import annotations

import asyncio
import socket
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from CortexOS.crew.llm import LLMResult, ToolCall
from CortexOS.crew.runtime import ACCESS_ASK_EVENT
from CortexOS.crew.server import create_app
from CortexOS.crew.session_grants import SessionGrantBook
from CortexOS.crew.workspace import GrantMissing, WorkspaceError, workspace_for
from tests.api_key_isolation import TEST_VIEWER_KEY
from tests.test_crew.conftest import FakeLLM, wait_run_done
from tests.test_crew.test_session_grant_dialog import browser_gate_unavailable

URL = "/crew/session-grants"
PROFILE_WIN = r"C:\Users\ops"
PROFILE_POSIX = "/home/ops"


def _tc(tool: str, **args: object) -> ToolCall:
    return ToolCall(id=f"c-{tool}", name=tool, args=dict(args))


def _tool_messages(rig: Any, space_id: str) -> list[str]:
    return [
        str(m.get("content") or "")
        for m in rig.store.list_messages(space_id)
        if m.get("role") == "tool"
    ]


def _drain(sub: Any) -> list[tuple[str, dict[str, Any]]]:
    out: list[tuple[str, dict[str, Any]]] = []
    while True:
        try:
            out.append(sub.queue.get_nowait())
        except asyncio.QueueEmpty:
            return out


def _asks(events: list[tuple[str, dict[str, Any]]]) -> list[dict[str, Any]]:
    return [data for name, data in events if name == ACCESS_ASK_EVENT]


async def _run_tool(rig: Any, space_id: str, call: ToolCall) -> tuple[str, list[dict[str, Any]]]:
    """Drive one manager tool call; return (tool message, access_ask events)."""
    sub = rig.bus.subscribe(space_id)
    before = len(_tool_messages(rig, space_id))
    rig.llm.manager.append(LLMResult(tool_calls=[call]))
    rig.llm.manager.append(LLMResult(text="done"))
    try:
        await rig.runtime.on_user_message(space_id, f"run {call.name}")
        await wait_run_done(rig.runtime, space_id)
        events = _drain(sub)
    finally:
        rig.bus.unsubscribe(space_id, sub)
    msgs = _tool_messages(rig, space_id)
    assert len(msgs) > before, "tool call never reached the transcript"
    return msgs[before], _asks(events)


@pytest.fixture()
def laptop(tmp_path: Path) -> dict[str, Path]:
    work = tmp_path / "laptop" / "work"
    outside = tmp_path / "laptop" / "outside"
    home = tmp_path / "home" / "ops"
    for d in (work / "sub", outside / "deep", home / "AppData"):
        d.mkdir(parents=True)
    (work / "notes.md").write_text("granted notes", encoding="utf-8")
    (work / "History").write_text("visited-urls-blob", encoding="utf-8")
    (outside / "secret.txt").write_text("ungranted secret", encoding="utf-8")
    (outside / "deep" / "more.txt").write_text("deeper secret", encoding="utf-8")
    (home / "top.txt").write_text("profile top", encoding="utf-8")
    (home / "AppData" / "cookie.txt").write_text("appdata", encoding="utf-8")
    return {"work": work, "outside": outside, "home": home, "root": tmp_path}


# ---- runtime: the ask is raised only for a grantable refusal ---------------------


@pytest.mark.asyncio
async def test_missing_grant_read_emits_one_access_ask_for_the_parent_folder(rig, laptop) -> None:
    space = rig.store.create_space("HQ")
    secret = laptop["outside"] / "secret.txt"
    out, asks = await _run_tool(rig, space["id"], _tc("ws_read", path=str(secret)))
    # the transcript still says why, and never the content
    assert out.startswith("DENIED") and "R-0011" in out
    assert "no folder grant with decision allow" in out
    assert "ungranted secret" not in out
    # exactly one ask, for the folder containing the file, keyed by the space
    assert len(asks) == 1
    ask = asks[0]
    assert ask["space_id"] == space["id"]
    assert ask["folder"] == str(laptop["outside"])
    assert ask["tool"] == "ws_read"
    assert str(secret) in ask["reason"] and "ws_read" in ask["reason"]
    assert "Allow" in ask["reason"] and "Cancel" in ask["reason"]
    assert "ungranted secret" not in ask["reason"]


@pytest.mark.asyncio
async def test_ls_and_glob_ask_for_the_directory_itself(rig, laptop) -> None:
    space = rig.store.create_space("HQ")
    out, asks = await _run_tool(rig, space["id"], _tc("ws_ls", path=str(laptop["outside"])))
    assert out.startswith("DENIED") and "R-0011" in out and "secret.txt" not in out
    assert [a["folder"] for a in asks] == [str(laptop["outside"])]
    assert asks[0]["tool"] == "ws_ls"
    out, asks = await _run_tool(
        rig, space["id"], _tc("ws_glob", pattern=str(laptop["outside"] / "deep" / "*.txt"))
    )
    assert out.startswith("DENIED") and "R-0011" in out and "more.txt" not in out
    assert [a["folder"] for a in asks] == [str(laptop["outside"] / "deep")]
    assert asks[0]["tool"] == "ws_glob"
    # a file that does not exist yet still asks for its containing folder
    out, asks = await _run_tool(
        rig, space["id"], _tc("ws_read", path=str(laptop["outside"] / "deep" / "nope.txt"))
    )
    assert out.startswith("DENIED") and "R-0011" in out
    assert [a["folder"] for a in asks] == [str(laptop["outside"] / "deep")]


@pytest.mark.asyncio
async def test_each_refusal_asks_once_and_a_granted_read_asks_nothing(rig, laptop) -> None:
    space = rig.store.create_space("HQ")
    secret = laptop["outside"] / "secret.txt"
    _, first = await _run_tool(rig, space["id"], _tc("ws_read", path=str(secret)))
    _, second = await _run_tool(rig, space["id"], _tc("ws_read", path=str(secret)))
    assert len(first) == 1 and len(second) == 1
    assert first[0]["folder"] == second[0]["folder"] == str(laptop["outside"])
    # after an Allow the same read succeeds and asks nothing
    rig.runtime.session_grants.record(space["id"], kind="folder", path=str(laptop["outside"]))
    out, asks = await _run_tool(rig, space["id"], _tc("ws_read", path=str(secret)))
    assert "ungranted secret" in out
    assert asks == []


@pytest.mark.asyncio
async def test_never_grantable_refusals_emit_no_ask(rig, laptop, settings) -> None:
    home = laptop["home"]
    rig.runtime.session_grants = SessionGrantBook(profile_roots=[str(home)])
    rig.runtime.session_grants.record(
        rig.store.create_space("other")["id"], kind="folder", path=str(laptop["work"])
    )
    space = rig.store.create_space("HQ")
    rig.runtime.session_grants.record(space["id"], kind="folder", path=str(laptop["work"]))
    cases: list[tuple[str, ToolCall, str]] = [
        ("filesystem root", _tc("ws_ls", path="/"), "R-0011"),
        ("drive root", _tc("ws_ls", path="C:\\"), "R-0011"),
        ("profile root", _tc("ws_ls", path=str(home)), "user profile root is not a grantable"),
        ("file directly under the profile root", _tc("ws_read", path=str(home / "top.txt")), "R-0011"),
        (
            "dotdot",
            _tc("ws_read", path=str(laptop["work"] / ".." / "outside" / "secret.txt")),
            "'..' traversal is refused",
        ),
        (
            "history db inside a granted folder",
            _tc("ws_read", path=str(laptop["work"] / "History")),
            "browser history database",
        ),
        (
            "write outside the space",
            _tc("ws_write", path=str(laptop["outside"] / "new.txt"), content="x"),
            "admits reads only",
        ),
        (
            "edit outside the space",
            _tc("ws_edit", path=str(laptop["work"] / "notes.md"), old="granted", new="x"),
            "admits reads only",
        ),
        ("relative escape", _tc("ws_read", path="../secrets.txt"), "escapes workspace"),
    ]
    for label, call, reason in cases:
        out, asks = await _run_tool(rig, space["id"], call)
        assert out.startswith("DENIED"), label
        assert reason in out, label
        assert asks == [], f"{label}: an ask was raised for a refusal no Allow can lift"
    for leaked in ("profile top", "appdata", "visited-urls-blob", "ungranted secret"):
        assert leaked not in "\n".join(_tool_messages(rig, space["id"]))
    assert not (laptop["outside"] / "new.txt").exists()
    assert (laptop["work"] / "notes.md").read_text(encoding="utf-8") == "granted notes"
    # ungranted AppData under the profile is grantable: it asks for AppData, not the profile
    out, asks = await _run_tool(
        rig, space["id"], _tc("ws_read", path=str(home / "AppData" / "cookie.txt"))
    )
    assert out.startswith("DENIED") and "no folder grant with decision allow" in out
    assert [a["folder"] for a in asks] == [str(home / "AppData")]


# ---- workspace: the refusal kind is structural, not message text -------------------


def test_grant_missing_is_a_workspace_error_carrying_the_folder(tmp_path: Path, laptop) -> None:
    book = SessionGrantBook(profile_roots=[str(laptop["home"]), PROFILE_WIN])
    ws = workspace_for(tmp_path / "crew", "s1", grants=book, session_id="s1")
    secret = laptop["outside"] / "secret.txt"
    with pytest.raises(GrantMissing) as info:
        ws.read(str(secret))
    assert isinstance(info.value, WorkspaceError)
    assert info.value.folder == str(laptop["outside"])
    assert "R-0011" in str(info.value) and "no folder grant with decision allow" in str(info.value)
    with pytest.raises(GrantMissing) as info:
        ws.ls(str(laptop["outside"]))
    assert info.value.folder == str(laptop["outside"])
    # a file straight under the profile root: still GrantMissing, but the folder
    # can never be granted, so there is nothing to ask for
    with pytest.raises(GrantMissing) as info:
        ws.read(str(laptop["home"] / "top.txt"))
    assert info.value.folder is None
    # never-grantable refusals are plain WorkspaceError, not GrantMissing
    for bad in (str(laptop["home"]), "/", "C:\\", str(laptop["work"] / ".." / "x")):
        with pytest.raises(WorkspaceError) as plain:
            ws.ls(bad)
        assert not isinstance(plain.value, GrantMissing), bad
    with pytest.raises(WorkspaceError) as plain:
        ws.write(str(laptop["outside"] / "new.txt"), "x")
    assert not isinstance(plain.value, GrantMissing)
    # a Windows-style path cannot be resolved on this POSIX host, so the book
    # refuses it before any lookup: a plain WorkspaceError, nothing to ask for.
    # The Windows parent rule (C:\\top.txt asks nothing, C:\\a\\b.txt asks C:\\a)
    # is asserted on the derivation itself below, string-level.
    with pytest.raises(WorkspaceError) as plain:
        ws.read(r"C:\Users\ops\Documents\Q3.xlsx")
    assert not isinstance(plain.value, GrantMissing)
    assert ws._ask_folder(r"C:\Users\ops\Documents\Q3.xlsx", tmp_path / "nope") == (
        r"C:\Users\ops\Documents"
    )
    assert ws._ask_folder(r"C:\top.txt", tmp_path / "nope") is None
    assert ws._ask_folder(r"C:\Users\ops\top.txt", tmp_path / "nope") is None  # profile root


# ---- real browser: the event travels the real channel into the dialog ---------------


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@pytest.fixture()
def served(settings, crew_env, monkeypatch: pytest.MonkeyPatch) -> Iterator[SimpleNamespace]:
    uvicorn = pytest.importorskip("uvicorn")
    monkeypatch.setenv("USERPROFILE", PROFILE_WIN)
    monkeypatch.setenv("HOME", PROFILE_POSIX)
    llm = FakeLLM()
    app = create_app(settings, llm_chat=llm)
    port = _free_port()
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", lifespan="on")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.time() + 15
    while not server.started:
        if time.time() > deadline or not thread.is_alive():
            raise AssertionError("uvicorn did not start")
        time.sleep(0.05)
    try:
        yield SimpleNamespace(base=f"http://127.0.0.1:{port}", app=app, crew=app.state.crew, llm=llm)
    finally:
        server.should_exit = True
        thread.join(10)


@pytest.fixture(scope="module")
def browser():
    # Same gate as test_session_grant_dialog.py: skip locally, fail where CI
    # sets CORTEX_BROWSER_GATE=required, so the two files behave identically.
    try:
        from playwright.sync_api import Error as PlaywrightError
        from playwright.sync_api import sync_playwright
    except ImportError:
        browser_gate_unavailable("python-playwright not installed: browser gate NOT run")

    with sync_playwright() as pw:
        try:
            chromium = pw.chromium.launch()
        except PlaywrightError as exc:  # pragma: no cover - environment dependent
            browser_gate_unavailable(f"Chromium could not launch: browser gate NOT run: {exc}")
        try:
            yield chromium
        finally:
            chromium.close()


@pytest.fixture()
def page(browser, served):
    import httpx

    context = browser.new_context()
    # #265: the page sends the operator's key from this tab's sessionStorage.
    context.add_init_script(f"sessionStorage.setItem('cortex.crew.apiKey', '{TEST_VIEWER_KEY}')")
    pg = context.new_page()
    posts: list[dict] = []
    pg.on(
        "request",
        lambda req: posts.append(req.post_data_json)
        if req.method == "POST" and req.url.endswith(URL)
        else None,
    )
    pg.goto(served.base + "/")
    pg.wait_for_function("typeof window.crewAskAccess === 'function'")
    # boot() selects the first space asynchronously; the test reads state.spaceId
    # and needs the SSE stream for it open before the runtime emits anything.
    pg.wait_for_function("state.spaceId !== null")
    pg.wait_for_function("() => !!state.spaceId && !!state.source")
    try:
        yield SimpleNamespace(
            pg=pg,
            posts=posts,
            http=httpx.Client(base_url=served.base, timeout=10, headers={"X-API-Key": TEST_VIEWER_KEY}),
            llm=served.llm,
            space_id=pg.evaluate("() => state.spaceId"),
        )
    finally:
        context.close()


def _closed(pg) -> None:
    pg.wait_for_function("() => !document.getElementById('accessAsk').open")


def _refuse_read(page, path: str, *, times: int = 1) -> None:
    """Script the manager to read ``path`` ``times`` times in one run and post
    the user message over HTTP: the refusal, the event and the dialog are all
    the real thing."""
    for _ in range(times):
        page.llm.manager.append(LLMResult(tool_calls=[_tc("ws_read", path=path)]))
    page.llm.manager.append(LLMResult(text="done"))
    resp = page.http.post(f"/crew/spaces/{page.space_id}/messages", json={"text": "read it"})
    assert resp.status_code == 200, resp.text


def _transcript(page) -> list[str]:
    rows = page.http.get(f"/crew/spaces/{page.space_id}/messages").json()
    return [str(m.get("content") or "") for m in rows if m.get("role") == "tool"]


def test_browser_missing_grant_read_opens_the_ask_and_cancel_posts_cancel(page, laptop) -> None:
    pg = page.pg
    secret = laptop["outside"] / "secret.txt"
    _refuse_read(page, str(secret))
    pg.wait_for_selector("#accessAsk[open]", timeout=15000)
    items = pg.locator("#accessAskItems .access-ask__item")
    assert items.count() == 1
    assert str(laptop["outside"]) in items.nth(0).inner_text()
    assert "folder" in items.nth(0).inner_text().lower()
    why = pg.locator("#accessAskWhy").inner_text()
    assert "ws_read" in why and str(secret) in why and "Allow" in why
    assert "Files stay on this device." in pg.locator("#accessAskLocal").inner_text()
    assert page.posts == [], "nothing is POSTed before the founder decides"
    pg.click("#accessAskCancel")
    _closed(pg)
    assert page.posts == [
        {
            "session_id": page.space_id,
            "kind": "folder",
            "decision": "cancel",
            "persist": False,
            "path": str(laptop["outside"]),
        }
    ]
    rows = page.http.get(URL, params={"session_id": page.space_id}).json()["grants"]
    assert [(r["path"], r["decision"]) for r in rows] == [(str(laptop["outside"]), "cancel")]
    # the transcript carries the refusal, never the content
    tools = _transcript(page)
    assert tools and tools[-1].startswith("DENIED") and "R-0011" in tools[-1]
    assert "ungranted secret" not in "\n".join(tools)


def test_browser_duplicate_ask_for_the_same_folder_opens_no_second_dialog(page, laptop) -> None:
    pg = page.pg
    secret = laptop["outside"] / "secret.txt"
    # two refusals for the same folder in one run: two access_ask events on the wire
    _refuse_read(page, str(secret), times=2)
    pg.wait_for_selector("#accessAsk[open]", timeout=15000)
    deadline = time.time() + 10
    while len(_transcript(page)) < 2:
        assert time.time() < deadline, "second refusal never reached the transcript"
        time.sleep(0.05)
    assert all("R-0011" in t for t in _transcript(page)[-2:])
    assert pg.locator("#accessAsk[open]").count() == 1
    assert pg.locator("#accessAskItems .access-ask__item").count() == 1
    pg.click("#accessAskCancel")
    _closed(pg)
    # the second event was ignored: no second dialog, one cancel row
    time.sleep(0.6)
    assert not pg.evaluate("() => document.getElementById('accessAsk').open")
    assert len(page.posts) == 1 and page.posts[0]["decision"] == "cancel"
    # after Cancel a fresh refusal asks again (the de-duplication is per open ask)
    _refuse_read(page, str(secret))
    pg.wait_for_selector("#accessAsk[open]", timeout=15000)
    assert str(laptop["outside"]) in pg.locator("#accessAskItems").inner_text()
    pg.keyboard.press("Escape")
    _closed(pg)
    assert len(page.posts) == 2 and page.posts[1]["decision"] == "cancel"


def test_browser_never_grantable_refusal_opens_nothing(page, laptop) -> None:
    pg = page.pg
    page.llm.manager.append(LLMResult(tool_calls=[_tc("ws_write", path=str(laptop["outside"] / "n.txt"), content="x")]))
    page.llm.manager.append(LLMResult(tool_calls=[_tc("ws_ls", path="/")]))
    page.llm.manager.append(LLMResult(text="done"))
    resp = page.http.post(f"/crew/spaces/{page.space_id}/messages", json={"text": "write it"})
    assert resp.status_code == 200, resp.text
    deadline = time.time() + 10
    while len(_transcript(page)) < 2:
        assert time.time() < deadline, "refusals never reached the transcript"
        time.sleep(0.05)
    assert all(t.startswith("DENIED") and "R-0011" in t for t in _transcript(page)[-2:])
    time.sleep(0.6)
    assert not pg.evaluate("() => document.getElementById('accessAsk').open")
    assert page.posts == []
    assert not (laptop["outside"] / "n.txt").exists()


def test_browser_page_asks_for_the_key_once_on_401_and_sends_it(browser, served) -> None:
    """#265: a gated Crew call from the page with no key prompts once, then
    retries with the key; the key stays in this tab's sessionStorage."""
    context = browser.new_context()
    try:
        pg = context.new_page()
        sent: list[str | None] = []
        pg.on(
            "request",
            lambda req: sent.append(req.headers.get("x-api-key"))
            if req.method == "POST" and req.url.endswith("/messages")
            else None,
        )
        prompts: list[str] = []

        def on_dialog(dialog) -> None:
            prompts.append(dialog.message)
            dialog.accept(TEST_VIEWER_KEY)

        pg.on("dialog", on_dialog)
        served.llm.manager.append(LLMResult(text="done"))
        pg.goto(served.base + "/")
        pg.wait_for_function("() => !!state.spaceId")
        ok = pg.evaluate(
            "async () => { await api('/crew/spaces/' + state.spaceId + '/messages',"
            " {method: 'POST', body: JSON.stringify({text: 'hi'})}); return true; }"
        )
        assert ok is True
        assert len(prompts) == 1 and "API key" in prompts[0]
        assert sent == [None, TEST_VIEWER_KEY]
        assert pg.evaluate("() => sessionStorage.getItem('cortex.crew.apiKey')") == TEST_VIEWER_KEY
    finally:
        context.close()
