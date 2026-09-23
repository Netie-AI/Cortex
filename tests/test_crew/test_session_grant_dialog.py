"""EPIC-GRANT-03 (#204): the Allow / Cancel session access dialog over GRANT-01.

Two layers, both on what the founder receives:

1. The served page (``GET /`` and ``GET /crew.css``): dialog markup with the
   a11y attributes, the local-device statement, real buttons, textContent-only
   painting of paths and titles, persist=false on every POST.
2. A real Chromium (python-playwright over the bundled browsers) against the
   Crew app served by uvicorn on a free port: a hostile window title renders as
   literal text, Cancel and Escape POST ``decision=cancel`` (never allow), a
   4xx from the store is painted inline on the refused item, focus moves into
   the dialog and back out.

HT1 (founder walks Allow and Cancel on real Documents plus one open window) is
a human gate and is not claimed here.
"""

from __future__ import annotations

import re
import socket
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from CortexOS.crew.server import create_app
from tests.test_crew.conftest import FakeLLM

UI = Path(__file__).resolve().parents[2] / "CortexOS" / "crew" / "ui"
URL = "/crew/session-grants"
PROFILE_WIN = r"C:\Users\ops"
PROFILE_POSIX = "/home/ops"
DOCS = r"C:\Users\ops\Documents"
HOSTILE_TITLE = "<img src=x onerror=alert(1)>"
JS_START = "// EPIC-GRANT-03 (#204): session access ask"
JS_END = '$("input").addEventListener("input"'


@pytest.fixture()
def client(settings, crew_env, monkeypatch: pytest.MonkeyPatch) -> Iterator[SimpleNamespace]:
    monkeypatch.setenv("USERPROFILE", PROFILE_WIN)
    monkeypatch.setenv("HOME", PROFILE_POSIX)
    app = create_app(settings, llm_chat=FakeLLM())
    with TestClient(app) as tc:
        yield SimpleNamespace(http=tc, crew=app.state.crew)


def _dialog_js(html: str) -> str:
    start = html.index(JS_START)
    end = html.index(JS_END, start)
    return html[start:end]


# ---- served page ---------------------------------------------------------------


def test_served_page_carries_the_access_dialog_with_a11y(client) -> None:
    page = client.http.get("/")
    assert page.status_code == 200
    html = page.text
    dialog = re.search(r"<dialog[^>]*id=\"accessAsk\"[^>]*>", html)
    assert dialog, "native <dialog id=accessAsk> missing"
    tag = dialog.group(0)
    assert 'role="dialog"' in tag
    assert 'aria-modal="true"' in tag
    assert 'aria-labelledby="accessAskTitle"' in tag
    assert "aria-describedby=" in tag and "accessAskWhy" in tag and "accessAskLocal" in tag
    assert 'id="accessAskTitle"' in html
    assert 'id="accessAskWhy"' in html
    assert 'id="accessAskItems"' in html
    # real buttons, Cancel first and never hidden behind Allow
    assert '<button class="ghost deny" id="accessAskCancel" type="button">Cancel</button>' in html
    assert '<button class="ok" id="accessAskAllow" type="button">Allow for this session</button>' in html
    assert html.index('id="accessAskCancel"') < html.index('id="accessAskAllow"')
    # the buyer-visible statement: local, reads only, no write promise
    assert "Files stay on this device." in html
    assert "Allow grants reads only" in html
    assert 'id="accessAskLocal"' in html
    local = re.search(r'id="accessAskLocal">(.*?)</p>', html)
    assert local and "write access" not in local.group(1).lower()
    assert "asks to read these" in html
    # GRANT-02 keys grants by space id: the entry point defaults to it
    assert "request.session_id || state.spaceId" in html
    assert 'id="accessAskRefused"' in html and 'role="alert"' in html
    css = client.http.get("/crew.css")
    assert css.status_code == 200
    assert ".access-ask" in css.text
    assert ".access-ask::backdrop" in css.text
    # live tokens only inside the block, no hard-coded chrome colours
    block = css.text[css.text.index(".access-ask {") : css.text.index(".scrim {")]
    assert "var(--hitl)" in block and "var(--dead)" in block and "var(--scrim)" in block
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b", block), "hard-coded colour in the dialog chrome"
    # focus ring is the shared one
    assert ":focus-visible" in css.text


def test_dialog_js_paints_with_textcontent_and_posts_session_only(client) -> None:
    html = client.http.get("/").text
    js = _dialog_js(html)
    assert "window.crewAskAccess = crewAskAccess" in js
    assert "textContent" in js
    assert "innerHTML" not in js
    assert "insertAdjacentHTML" not in js
    assert "outerHTML" not in js
    assert "persist: false" in js
    assert "persist: true" not in js
    assert 'ACCESS_ASK_URL = "/crew/session-grants"' in js
    assert 'addEventListener("cancel"' in js  # Escape routes through Cancel
    assert 'addEventListener("close"' in js  # platform close is still Cancel
    assert "showModal()" in js
    # Cancel never posts allow: the only allow literal is the Allow button handler
    assert js.count('accessAskSettle("allow")') == 1
    assert '$("accessAskAllow").onclick = () => accessAskSettle("allow")' in js
    # no second store: the only endpoint the dialog talks to is GRANT-01
    assert "/crew/session-grants" in js
    assert "localStorage" not in js


# ---- real browser --------------------------------------------------------------


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@pytest.fixture()
def served(settings, crew_env, monkeypatch: pytest.MonkeyPatch) -> Iterator[SimpleNamespace]:
    uvicorn = pytest.importorskip("uvicorn")
    monkeypatch.setenv("USERPROFILE", PROFILE_WIN)
    monkeypatch.setenv("HOME", PROFILE_POSIX)
    app = create_app(settings, llm_chat=FakeLLM())
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
        yield SimpleNamespace(base=f"http://127.0.0.1:{port}", app=app, crew=app.state.crew)
    finally:
        server.should_exit = True
        thread.join(10)


@pytest.fixture(scope="module")
def browser():
    playwright = pytest.importorskip(
        "playwright.sync_api", reason="python-playwright not installed: browser gate NOT run"
    )
    from playwright.sync_api import Error as PlaywrightError

    with playwright.sync_playwright() as pw:
        try:
            chromium = pw.chromium.launch()
        except PlaywrightError as exc:  # pragma: no cover - environment dependent
            pytest.skip(f"Chromium could not launch: browser gate NOT run: {exc}")
        try:
            yield chromium
        finally:
            chromium.close()


@pytest.fixture()
def page(browser, served):
    import httpx

    context = browser.new_context()
    pg = context.new_page()
    posts: list[dict] = []
    alerts: list[str] = []
    pg.on(
        "request",
        lambda req: posts.append(req.post_data_json)
        if req.method == "POST" and req.url.endswith(URL)
        else None,
    )
    pg.on("dialog", lambda d: (alerts.append(d.message), d.dismiss()))
    pg.goto(served.base + "/")
    pg.wait_for_function("typeof window.crewAskAccess === 'function'")
    try:
        yield SimpleNamespace(
            pg=pg,
            posts=posts,
            alerts=alerts,
            http=httpx.Client(base_url=served.base, timeout=10),
        )
    finally:
        context.close()


def _grants(http, session_id: str) -> list[dict]:
    resp = http.get(URL, params={"session_id": session_id})
    assert resp.status_code == 200, resp.text
    return resp.json()["grants"]


def _ask(pg, req: dict) -> None:
    pg.evaluate("(req) => { window.__ask = window.crewAskAccess(req); }", req)
    pg.wait_for_selector("#accessAsk[open]")


def _closed(pg) -> None:
    # a closed <dialog> is hidden, so wait on the open flag rather than visibility
    pg.wait_for_function("() => !document.getElementById('accessAsk').open")


def _result(pg) -> str:
    return pg.evaluate("() => window.__ask")


def test_browser_cancel_posts_cancel_and_paints_hostile_title_as_text(page) -> None:
    pg = page.pg
    pg.focus("#clearChat")
    _ask(
        pg,
        {
            "session_id": "ht1-cancel",
            "reason": "Read the quarterly deck and the open Excel window to draft the summary.",
            "folders": [DOCS],
            "windows": [{"title": HOSTILE_TITLE, "pid": 4242}],
            "files": [DOCS + r"\Q3.xlsx"],
        },
    )
    dlg = pg.locator("#accessAsk")
    assert dlg.get_attribute("role") == "dialog"
    assert dlg.get_attribute("aria-labelledby") == "accessAskTitle"
    assert "accessAskWhy" in (dlg.get_attribute("aria-describedby") or "")
    # focus moved into the dialog, onto Cancel (the safe default)
    assert pg.evaluate("() => document.activeElement && document.activeElement.id") == "accessAskCancel"
    # exact paths, the title and the reason are visible, the title is literal text
    items = pg.locator("#accessAskItems .access-ask__item")
    assert items.count() == 3
    texts = [items.nth(i).inner_text() for i in range(3)]
    assert DOCS in texts[0]
    assert DOCS + r"\Q3.xlsx" in texts[1]
    assert HOSTILE_TITLE in texts[2] and "pid 4242" in texts[2]
    assert pg.locator("#accessAskItems img").count() == 0
    assert pg.evaluate(
        "() => document.querySelector('#accessAskItems .access-ask__what:last-of-type') !== null"
    )
    assert pg.locator("#accessAskWhy").inner_text().startswith("Read the quarterly deck")
    assert "Files stay on this device." in pg.locator("#accessAskLocal").inner_text()
    assert pg.locator("#accessAskNote").is_hidden()
    pg.click("#accessAskCancel")
    _closed(pg)
    assert _result(pg) == "cancel"
    assert page.alerts == [], "hostile title executed"
    # every item was POSTed with decision=cancel, persist=false, never allow
    bodies = page.posts
    assert len(bodies) == 3
    assert {b["decision"] for b in bodies} == {"cancel"}
    assert all(b["persist"] is False for b in bodies)
    assert [b["kind"] for b in bodies] == ["folder", "office_file", "window"]
    assert bodies[2]["title"] == HOSTILE_TITLE and bodies[2]["pid"] == 4242
    # the store the next reader sees: cancel rows only, no allow anywhere
    rows = _grants(page.http, "ht1-cancel")
    assert rows and {r["decision"] for r in rows} == {"cancel"}
    assert any(r.get("title") == HOSTILE_TITLE for r in rows)
    # the office_file cancel is refused by the store (its folder was never allowed);
    # a refusal to record a cancel grants nothing, so it is surfaced, not silently dropped
    assert [r["kind"] for r in rows] == ["folder", "window"]
    last = pg.evaluate("() => window.crewAskAccess.last")
    assert last["decision"] == "cancel"
    assert [r["kind"] for r in last["refused"]] == ["office_file"]
    assert "outside every folder" in last["refused"][0]["reason"]
    assert "outside every folder" in pg.locator("#toasts .toast--bad").first.inner_text()
    # focus returned to where it was
    assert pg.evaluate("() => document.activeElement && document.activeElement.id") == "clearChat"


def test_browser_escape_is_cancel(page) -> None:
    pg = page.pg
    _ask(pg, {"session_id": "ht1-esc", "reason": "why", "folders": [DOCS]})
    pg.keyboard.press("Escape")
    _closed(pg)
    assert _result(pg) == "cancel"
    assert page.posts and {b["decision"] for b in page.posts} == {"cancel"}
    assert page.posts[0]["persist"] is False
    assert {r["decision"] for r in _grants(page.http, "ht1-esc")} == {"cancel"}


def test_browser_allow_paints_store_refusal_inline(page) -> None:
    pg = page.pg
    _ask(
        pg,
        {
            "session_id": "ht1-allow",
            "reason": "Index Documents.",
            "folders": [DOCS, "C:\\"],
            "destination": "local",
        },
    )
    pg.click("#accessAskAllow")
    pg.wait_for_selector("#accessAskRefused:not([hidden])")
    refused = pg.locator("#accessAskItems .access-ask__item--refused")
    assert refused.count() == 1
    assert "C:\\" in refused.inner_text()
    assert "drive root" in refused.inner_text()
    assert "1 item(s) refused" in pg.locator("#accessAskRefused").inner_text()
    assert pg.locator("#accessAskAllow").is_hidden()
    assert pg.locator("#accessAskCancel").inner_text() == "Close"
    assert pg.locator("#accessAsk[open]").count() == 1
    pg.click("#accessAskCancel")
    _closed(pg)
    assert _result(pg) == "allow"
    last = pg.evaluate("() => window.crewAskAccess.last")
    assert [g["path"] for g in last["granted"]] == [DOCS]
    assert last["refused"][0]["path"] == "C:\\" and "drive root" in last["refused"][0]["reason"]
    rows = _grants(page.http, "ht1-allow")
    assert [(r["path"], r["decision"], r["persist"]) for r in rows] == [(DOCS, "allow", False)]


def test_browser_session_id_defaults_to_the_current_space_id(page) -> None:
    """GRANT-02 checks grants by Crew space id: an Allow under any other id opens nothing."""
    pg = page.pg
    space = page.http.post("/crew/spaces", json={"title": "HT1"}).json()
    space_id = space["id"]
    pg.evaluate("(id) => { state.spaceId = id; }", space_id)
    _ask(pg, {"reason": "Read Documents for this space.", "folders": [DOCS]})
    pg.click("#accessAskAllow")
    _closed(pg)
    assert _result(pg) == "allow"
    assert len(page.posts) == 1
    assert page.posts[0]["session_id"] == space_id
    assert page.posts[0] == {
        "session_id": space_id,
        "kind": "folder",
        "decision": "allow",
        "persist": False,
        "path": DOCS,
    }
    rows = _grants(page.http, space_id)
    assert [(r["path"], r["decision"], r["scope"]) for r in rows] == [(DOCS, "allow", "session")]
    # an explicit session_id still wins (a caller may ask for another space)
    _ask(pg, {"session_id": "explicit-1", "reason": "why", "folders": [DOCS]})
    pg.click("#accessAskCancel")
    _closed(pg)
    assert page.posts[-1]["session_id"] == "explicit-1"


def test_browser_named_destination_is_shown_only_when_not_local(page) -> None:
    pg = page.pg
    _ask(
        pg,
        {
            "session_id": "ht1-dest",
            "reason": "why",
            "folders": [{"path": DOCS, "destination": "cloud-worker-eu-1"}, DOCS + r"\Local"],
        },
    )
    items = pg.locator("#accessAskItems .access-ask__item")
    assert "to cloud-worker-eu-1" in items.nth(0).inner_text()
    assert "to " not in items.nth(1).inner_text()
    assert pg.locator("#accessAskNote").is_visible()
    assert "1 item(s) name a cloud worker destination" in pg.locator("#accessAskNote").inner_text()
    assert "Files stay on this device." in pg.locator("#accessAskLocal").inner_text()
    pg.click("#accessAskCancel")
    _closed(pg)
    assert _result(pg) == "cancel"
