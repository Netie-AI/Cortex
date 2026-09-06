"""AppShell catalog: nav + Apps launcher + Control/Audit GET display."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from CortexOS.crew import appshell
from CortexOS.crew.server import create_app
from CortexOS.execution.rsf_eval import load_poison_run
from CortexOS.rsf import RSF_STATUSES
from tests.test_crew.conftest import FakeLLM

UI = Path(__file__).resolve().parents[2] / "CortexOS" / "crew" / "ui"
APPSHELL_PY = Path(__file__).resolve().parents[2] / "CortexOS" / "crew" / "appshell.py"


@pytest.fixture()
def client(settings, crew_env) -> Iterator[SimpleNamespace]:
    fake = FakeLLM()
    app = create_app(settings, llm_chat=fake)
    with TestClient(app) as tc:
        yield SimpleNamespace(http=tc, llm=fake, app=app, crew=app.state.crew)


def test_catalog_nav_and_app_tiles() -> None:
    body = appshell.catalog(engine_url="http://127.0.0.1:8010")
    ids = [row["id"] for row in body["nav"]]
    assert ids == [
        "home",
        "crew",
        "agents",
        "control",
        "apps",
        "audit",
        "assign",
        "memory",
        "settings",
    ]
    slugs = [row["slug"] for row in body["apps"]]
    assert slugs == [
        "dms",
        "constructor",
        "airgpt",
        "openvault",
        "pointer",
        "space",
        "control",
    ]
    control = next(row for row in body["apps"] if row["slug"] == "control")
    assert control["spawn"] is False
    assert "deep-link" in control["open"]
    assert "iframe" in control["open"]
    assert "panel" in control["open"]
    assert body["control"]["display_only"] is True
    assert body["control"]["converse"] is False
    assert body["control"]["spawn"] is False
    assert body["banner"] == "Display only F-0030"
    palette_ids = {row["id"] for row in body["palette"]}
    assert {"home", "crew", "apps", "control", "audit", "settings"} <= palette_ids
    ctor = next(row for row in body["apps"] if row["slug"] == "constructor")
    assert ctor["url"].endswith("/cortex/constructor/")


def test_health_unprobed_when_live_probes_off(crew_env) -> None:
    body = appshell.health(engine_url="http://127.0.0.1:8010")
    assert body["live_probes"] is False
    control = next(row for row in body["apps"] if row["slug"] == "control")
    assert control["ok"] is False
    assert control["status"] == "unprobed"
    assert control["spawn"] is False


def test_control_display_never_posts_and_refuses_banned_paths() -> None:
    seen: list[str] = []

    def getter(url: str, timeout: float) -> tuple[int, str, str]:
        seen.append(url)
        return 200, '{"belt": true}', "application/json"

    snap = appshell.control_display(get=getter, live=True)
    assert snap["display_only"] is True
    assert snap["banner"] == "Display only F-0030"
    assert snap["converse"] is False
    assert snap["spawn"] is False
    assert seen
    joined = " ".join(seen)
    assert "/run" not in joined
    assert "/goal" not in joined
    assert "/route" not in joined
    assert "/secrets" not in joined
    assert "converse" not in joined
    assert any(url.endswith("/v1/belt") for url in seen)

    banned = appshell.control_display(path="/run", get=getter, live=True)
    assert banned["refused"] is True
    assert banned["ok"] is False
    assert appshell.allowed_control_path("/secrets") is False
    assert appshell.allowed_control_path("/v1/belt") is True
    spawn = appshell.refuse_control_spawn()
    assert spawn["ok"] is False and spawn["spawn"] is False


def test_audit_payload_is_honest_statuses() -> None:
    empty = appshell.audit_payload()
    assert empty["invented_certified"] is False
    assert empty["statuses"] == ["CERTIFIED", "ABSTAIN", "REFUSE"]
    stages = empty["audit"]["stages"]
    assert [row["status"] for row in stages] == ["ABSTAIN", "ABSTAIN", "ABSTAIN", "ABSTAIN"]
    assert all(row["status"] in RSF_STATUSES for row in stages)
    poison = load_poison_run()
    shown = appshell.audit_payload(poison)
    assert shown["invented_certified"] is False
    classify = next(row for row in shown["audit"]["stages"] if row["stage"] == "classify")
    assert classify["status"] == "ABSTAIN"
    assert classify["displayed_certified"] is False


def test_http_appshell_routes(client) -> None:
    cat = client.http.get("/crew/appshell").json()
    assert cat["ok"] is True
    assert {row["id"] for row in cat["nav"]} >= {"home", "crew", "apps", "control", "audit"}
    health = client.http.get("/crew/appshell/health").json()
    assert all(row["status"] == "unprobed" for row in health["apps"])
    control = client.http.get("/crew/appshell/control").json()
    assert control["banner"] == "Display only F-0030"
    assert control["display_only"] is True
    banned = client.http.get("/crew/appshell/control", params={"path": "/run"})
    assert banned.status_code == 403
    posted = client.http.post("/crew/appshell/control")
    assert posted.status_code == 405
    spawn = client.http.post("/crew/appshell/spawn")
    assert spawn.status_code == 403
    audit = client.http.get("/crew/appshell/audit").json()
    assert audit["invented_certified"] is False
    assert {row["status"] for row in audit["audit"]["stages"]} <= set(RSF_STATUSES)
    page = client.http.get("/")
    assert page.status_code == 200
    assert 'id="appshell"' in page.text
    assert 'aria-label="AppShell"' in page.text
    assert 'data-nav="apps"' in page.text
    assert "Display only F-0030" in page.text
    assert 'id="appTiles"' in page.text
    assert 'id="controlFrame"' in page.text
    assert 'id="shellAudit"' in page.text
    assert "pageHits" in page.text
    assert "/crew/appshell" in page.text
    assert "goNav" in page.text
    assert 'data-open="spawn"' not in page.text
    css = client.http.get("/crew.css")
    assert b"--shell-nav-w" in css.content
    assert b"--shell-bottom-nav-h" in css.content
    assert b"appshell--open" in css.content
    manifest = client.http.get("/appshell.webmanifest")
    assert manifest.status_code == 200
    assert "standalone" in manifest.text
    src = APPSHELL_PY.read_text(encoding="utf-8")
    html = (UI / "index.html").read_text(encoding="utf-8")
    for banned_word in ("guaca", "rakazo", "mybot", "openwillow"):
        assert banned_word not in src.lower()
        assert banned_word not in html.lower()
    assert "method=\"POST\"" not in src
    assert "method='POST'" not in src
