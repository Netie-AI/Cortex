"""APPSHELL-BUYER-REACH: documented host path + leave-machine fail-closed prove.

Assertions are on the operator artifacts (R-0001): served AppShell HTML,
GET /appshell host path, host catalog stamps, and leave-gate refusals.
Does not invent live-host COMPLETE or GitHub CI :5000/:8020 green.
Tunnel :8020 leftover remains NOT buyer COMPLETE.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from CortexOS.crew import appshell, appshell_host
from CortexOS.crew.appshell_host_routes import mount_engine_appshell
from CortexOS.crew.server import create_app
from tests.test_crew.conftest import FakeLLM

UI = Path(__file__).resolve().parents[2] / "CortexOS" / "crew" / "ui" / "index.html"
HOST_PY = Path(__file__).resolve().parents[2] / "CortexOS" / "crew" / "appshell_host.py"
ROUTES_PY = Path(__file__).resolve().parents[2] / "CortexOS" / "crew" / "appshell_host_routes.py"
API_APP = Path(__file__).resolve().parents[2] / "CortexOS" / "api" / "app.py"
PACK_HOST = Path(__file__).resolve().parents[2] / "packs" / "dms" / "appshell_host.py"
SERVER_PY = Path(__file__).resolve().parents[2] / "CortexOS" / "crew" / "server.py"


@pytest.fixture()
def client(settings, crew_env) -> Iterator[SimpleNamespace]:
    fake = FakeLLM()
    app = create_app(settings, llm_chat=fake)
    with TestClient(app) as tc:
        yield SimpleNamespace(http=tc, llm=fake, app=app, crew=app.state.crew)


def test_honesty_stamps_never_invent_complete() -> None:
    stamps = appshell_host.honesty()
    assert stamps["slice"] == "APPSHELL-BUYER-REACH"
    assert stamps["issue"] == 237
    assert stamps["parent_epic"] == 236
    assert stamps["appshell_pr"] == 195
    assert stamps["facts_issue"] == 232
    assert stamps["complete"] is False
    assert stamps["buyer_complete"] is False
    assert stamps["live_host_complete"] is False
    assert stamps["live_8020_ci"] is False
    assert stamps["live_5000_ci"] is False
    assert stamps["release"] is False
    assert stamps["issue_212_complete"] is False
    assert stamps["second_vault"] is False
    assert stamps["kills_8020"] is False
    assert stamps["control_post"] is False
    refused = appshell_host.refuse_invent_green(
        {
            "complete": True,
            "buyer_complete": True,
            "live_8020_ci": True,
            "tunnel_complete": True,
            "issue_212_complete": True,
            "trained_jepa": True,
            "second_vault": True,
            "kill_8020": True,
        }
    )
    assert refused["ok"] is False and refused["refused"] is True
    blob = " ".join(refused["reasons"]).lower()
    assert "live-host" in blob
    assert "212" in blob
    assert "tunnel" in blob
    assert "vault" in blob
    assert refused["live_8020_ci"] is False
    assert refused["buyer_complete"] is False


def test_catalog_documents_public_host_not_new_name_or_tunnel_complete() -> None:
    stamp = appshell_host.catalog_stamp()
    assert stamp["public_url"] == "https://app.netie.ai/appshell"
    assert stamp["host_path"] == "/appshell"
    assert stamp["engine_local_url"] == "http://127.0.0.1:8010/appshell"
    assert stamp["tunnel_leftover"] == "http://127.0.0.1:8020"
    assert stamp["tunnel_only"] is False
    assert stamp["tunnel_complete"] is False
    assert stamp["buyer_complete"] is False
    assert stamp["new_hostname"] is False
    assert stamp["same_host_as_constructor"] is True
    assert stamp["constructor_public"] == "https://app.netie.ai/cortex"
    assert stamp["control_display_only"] is True
    assert stamp["live_8020_ci"] is False
    assert "F-0030" in stamp["banner"]
    cat = appshell.catalog(engine_url="http://127.0.0.1:8010")
    assert cat["host"]["public_url"] == stamp["public_url"]
    assert cat["control"]["display_only"] is True
    assert cat["control"]["spawn"] is False


def test_hud_paints_host_path_and_control_chrome() -> None:
    html = UI.read_text(encoding="utf-8")
    hud = appshell_host.inspect_hud(html)
    assert hud["ok"] is True, hud["missing"]
    assert 'data-host-path="/appshell"' in html
    assert 'data-public-host="https://app.netie.ai/appshell"' in html
    assert "initShell()" in html
    assert "Crew converse APIs unavailable on this host path" in html


def test_loopback_bind_is_leftover_not_buyer_complete() -> None:
    loop = appshell_host.decide_bind("127.0.0.1")
    assert loop["allowed"] is True
    assert loop["ok"] is True
    assert loop["status"] == appshell_host.STATUS_TUNNEL_LEFTOVER
    assert loop["leaves_machine"] is False
    assert loop["buyer_complete"] is False
    assert loop["live_8020_ci"] is False
    assert appshell_host.bind_leaves_machine("localhost") is False
    assert appshell_host.bind_leaves_machine("0.0.0.0") is True


def test_public_bind_fail_closed_when_leave_denied() -> None:
    def denied(**kwargs: object) -> dict[str, object]:
        return {
            "ok": True,
            "allowed": False,
            "action": kwargs.get("action"),
            "destination": kwargs.get("destination"),
            "reasons": ["vault is sealed"],
        }

    out = appshell_host.decide_bind("0.0.0.0", check=denied)
    assert out["ok"] is False
    assert out["allowed"] is False
    assert out["status"] == appshell_host.STATUS_HOST_DENIED
    assert out["buyer_complete"] is False
    assert out["live_host_complete"] is False
    assert "sealed" in out["detail"]
    assert out["gate"]["destination"] == "appshell-host"
    assert out["gate"]["action"] == "leave"


def test_public_bind_fail_closed_when_gate_unreachable() -> None:
    def boom(**kwargs: object) -> dict[str, object]:
        raise OSError("OpenVault unreachable")

    out = appshell_host.decide_bind("app.netie.ai", check=boom)
    assert out["ok"] is False
    assert out["allowed"] is False
    assert out["status"] == appshell_host.STATUS_HOST_DENIED
    assert out["leaves_machine"] is True
    assert out["complete"] is False
    assert out["buyer_complete"] is False


def test_public_bind_allowed_is_not_ci_green() -> None:
    def allowed(**kwargs: object) -> dict[str, object]:
        return {"ok": True, "allowed": True, "action": "leave", "destination": "appshell-host"}

    out = appshell_host.decide_bind("0.0.0.0", check=allowed)
    assert out["allowed"] is True
    assert out["ok"] is True
    assert out["buyer_complete"] is False
    assert out["live_host_complete"] is False
    assert out["live_8020_ci"] is False
    assert out["release"] is False
    assert "not GitHub CI" in out["detail"]


def test_prove_live_fail_closed_when_probes_off(crew_env) -> None:
    def allowed(**kwargs: object) -> dict[str, object]:
        return {"ok": True, "allowed": True}

    report = appshell_host.prove_live(
        "https://app.netie.ai/appshell", check=allowed
    )
    assert report["ok"] is False
    assert report["status"] == appshell_host.STATUS_NOT_PROVEN
    assert report["host_up"] is False
    assert report["live_8020_ci"] is False
    assert report["buyer_complete"] is False
    assert "CREW_LIVE_PROBES=0" in report["detail"]


def test_prove_live_denied_does_not_claim_public_host(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CREW_LIVE_PROBES", "1")

    def denied(**kwargs: object) -> dict[str, object]:
        return {"ok": False, "allowed": False, "reasons": ["denied"]}

    seen: list[str] = []

    def getter(url: str, timeout: float) -> tuple[int, str, str]:
        seen.append(url)
        return 200, UI.read_text(encoding="utf-8"), "text/html"

    report = appshell_host.prove_live(
        "https://app.netie.ai/appshell", get=getter, check=denied
    )
    assert report["ok"] is False
    assert report["status"] == appshell_host.STATUS_HOST_DENIED
    assert report["host_up"] is False
    assert report["buyer_complete"] is False
    assert report["live_host_complete"] is False
    assert not seen  # fail-closed: do not GET after deny
    assert "not invent-green public host" in report["detail"]


def test_prove_live_reachable_hud_is_not_ci_green(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CREW_LIVE_PROBES", "1")
    html = UI.read_text(encoding="utf-8")

    def allowed(**kwargs: object) -> dict[str, object]:
        return {"ok": True, "allowed": True}

    def getter(url: str, timeout: float) -> tuple[int, str, str]:
        return 200, html, "text/html"

    report = appshell_host.prove_live(
        "https://app.netie.ai/appshell", timeout=0.2, get=getter, check=allowed
    )
    assert report["host_up"] is True
    assert report["status"] == appshell_host.STATUS_HOST_REACHABLE
    assert report["ok"] is True
    assert report["hud"]["ok"] is True
    assert report["live_8020_ci"] is False
    assert report["live_5000_ci"] is False
    assert report["live_host_complete"] is False
    assert report["buyer_complete"] is False
    assert report["ci_live_host_green"] is False
    assert "not GitHub CI" in report["detail"]


def test_prove_live_closed_port_is_not_proven(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CREW_LIVE_PROBES", "1")
    report = appshell_host.prove_live("http://127.0.0.1:9/appshell", timeout=0.2)
    assert report["ok"] is False
    assert report["status"] == appshell_host.STATUS_NOT_PROVEN
    assert report["host_up"] is False
    assert report["buyer_complete"] is False
    assert report["kills_8020"] is False


def test_http_appshell_host_path_serves_control_crew_chrome(client) -> None:
    page = client.http.get("/appshell")
    assert page.status_code == 200
    hud = appshell_host.inspect_hud(page.text)
    assert hud["ok"] is True, hud["missing"]
    assert 'id="appshell"' in page.text
    assert "Display only F-0030" in page.text
    root = client.http.get("/")
    assert 'data-host-path="/appshell"' in root.text

    law = client.http.get("/crew/appshell/host").json()
    assert law["ok"] is True
    assert law["status"] == appshell_host.STATUS_DOCUMENTED
    assert law["public_url"] == "https://app.netie.ai/appshell"
    assert law["tunnel_complete"] is False
    assert law["buyer_complete"] is False
    assert law["live_8020_ci"] is False
    assert law["host_up"] is False
    assert law["mutate"] is False
    assert law["hud"]["ok"] is True
    assert law["ci_live_host_green"] is False

    live = client.http.get("/crew/appshell/host/live").json()
    assert live["ok"] is False
    assert live["status"] == appshell_host.STATUS_NOT_PROVEN
    assert live["buyer_complete"] is False
    assert live["live_host_complete"] is False

    posted = client.http.post("/crew/appshell/host")
    assert posted.status_code == 405
    live_post = client.http.post("/crew/appshell/host/live")
    assert live_post.status_code == 405
    control_post = client.http.post("/crew/appshell/control")
    assert control_post.status_code == 405
    spawn = client.http.post("/crew/appshell/spawn")
    assert spawn.status_code == 403
    banned = client.http.get("/crew/appshell/control", params={"path": "/secrets"})
    assert banned.status_code == 403

    cat = client.http.get("/crew/appshell").json()
    assert cat["host"]["public_url"] == "https://app.netie.ai/appshell"
    manifest = client.http.get("/appshell.webmanifest")
    assert manifest.status_code == 200
    assert "/appshell" in manifest.text


def test_engine_mount_is_buyer_path_without_tunnel(crew_env) -> None:
    """Same chrome on a FastAPI engine host -- tunnel is not the only path."""
    app = FastAPI()
    mount_engine_appshell(app)
    with TestClient(app) as http:
        page = http.get("/appshell")
        assert page.status_code == 200
        assert appshell_host.inspect_hud(page.text)["ok"] is True
        cat = http.get("/crew/appshell").json()
        assert cat["ok"] is True
        assert cat["host"]["engine_local_url"].endswith("/appshell")
        assert cat["control"]["display_only"] is True
        assert http.post("/crew/appshell/control").status_code == 405
        assert http.post("/crew/appshell/spawn").status_code == 403
        assert http.get("/crew/appshell/control", params={"path": "/run"}).status_code == 403
        host = http.get("/crew/appshell/host").json()
        assert host["tunnel_only"] is False
        assert host["buyer_complete"] is False
        css = http.get("/crew.css")
        assert css.status_code == 200
        assert b"--shell-nav-w" in css.content


def test_cli_denied_or_down_exits_not_proven(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("CREW_LIVE_PROBES", "1")
    monkeypatch.setenv("CREW_APPSHELL_HOST_URL", "http://127.0.0.1:9/appshell")
    code = appshell_host.main([])
    assert code == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == appshell_host.STATUS_NOT_PROVEN
    assert payload["live_8020_ci"] is False
    assert payload["buyer_complete"] is False


def test_module_does_not_kill_ports_or_paste_analogs() -> None:
    source = HOST_PY.read_text(encoding="utf-8")
    routes = ROUTES_PY.read_text(encoding="utf-8")
    api = API_APP.read_text(encoding="utf-8")
    pack = PACK_HOST.read_text(encoding="utf-8")
    server = SERVER_PY.read_text(encoding="utf-8")
    assert "os.kill" not in source
    assert "SIGTERM" not in source
    assert "subprocess" not in source
    assert "uvicorn" not in source
    assert "duckdb" not in source
    assert "packs." not in source
    assert "from n8n" not in source
    assert "import memgpt" not in source.lower()
    assert "R-0015" in source
    assert "method=\"POST\"" not in source
    assert "Request(url, method=\"GET\")" in source or 'method="GET"' in source
    assert "CortexOS.crew" not in api
    assert "register_appshell_host_routes" in api
    assert "mount_engine_appshell" in pack
    assert "decide_bind" in server
    assert "include_in_schema=False" in routes
    assert "Display only F-0030" in source
    assert "/run" not in source.lower() or "never POST run" in source
