"""CREW-8020-FACTS: facts.md list/save/search/export + HUD + fail-closed live prove.

Assertions are on the operator artifacts (R-0001): served HUD HTML, HTTP
memory envelopes, facts.md on disk after clear, and the prove stamps.
Does not invent live-host COMPLETE or GitHub CI :8020 green.
"""

from __future__ import annotations

import json
import time
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from CortexOS.crew import facts_prove
from CortexOS.crew.memory import FACTS_FILE, collection_for
from CortexOS.crew.server import create_app
from tests.test_crew.conftest import FakeLLM

UI = Path(__file__).resolve().parents[2] / "CortexOS" / "crew" / "ui" / "index.html"
PROVE_PY = Path(__file__).resolve().parents[2] / "CortexOS" / "crew" / "facts_prove.py"


@pytest.fixture()
def client(settings, crew_env) -> Iterator[SimpleNamespace]:
    fake = FakeLLM()
    app = create_app(settings, llm_chat=fake)
    with TestClient(app) as tc:
        yield SimpleNamespace(http=tc, llm=fake, app=app, crew=app.state.crew)


def _wait_idle(client: SimpleNamespace, space_id: str, seconds: float = 5.0) -> None:
    deadline = time.time() + seconds
    while client.crew.runtime._space_run.get(space_id):
        if time.time() > deadline:
            break
        time.sleep(0.02)


def test_honesty_stamps_never_invent_complete() -> None:
    stamps = facts_prove.honesty()
    assert stamps["slice"] == "CREW-8020-FACTS"
    assert stamps["issue"] == 232
    assert stamps["parent_epic"] == 116
    assert stamps["scale_build_pr"] == 210
    assert stamps["scale_build_sha"] == "c0385603"
    assert stamps["complete"] is False
    assert stamps["live_host_complete"] is False
    assert stamps["live_8020_ci"] is False
    assert stamps["live_5000_ci"] is False
    assert stamps["release"] is False
    assert stamps["issue_212_complete"] is False
    assert stamps["trained_jepa"] is False
    assert stamps["langgraph"] is False
    assert stamps["n8n"] is False
    assert stamps["memgpt"] is False
    assert stamps["kills_8020"] is False
    refused = facts_prove.refuse_invent_green(
        {
            "complete": True,
            "live_8020_ci": True,
            "issue_212_complete": True,
            "trained_jepa": True,
            "kill_8020": True,
        }
    )
    assert refused["ok"] is False and refused["refused"] is True
    blob = " ".join(refused["reasons"]).lower()
    assert "live-host" in blob and "212" in blob and "jepa" in blob and "8020" in blob
    assert refused["live_8020_ci"] is False
    assert refused["complete"] is False


def test_prove_facts_store_list_save_search_export(tmp_path: Path) -> None:
    mem = collection_for(tmp_path / "crew", "s1", scope="space")
    out = facts_prove.prove_facts_store(mem)
    assert out["ok"] is True
    assert out["file"] == "facts.md"
    assert out["listed"] == ["crew-port"]
    assert out["searched"] == ["crew-port"]
    assert "# Crew facts" in out["exported"]
    assert "8020" in out["exported"]
    assert (mem.root / FACTS_FILE).is_file()
    assert out["live_8020_ci"] is False
    assert out["complete"] is False


def test_hud_paints_memory_nav_facts_and_scale_build() -> None:
    html = UI.read_text(encoding="utf-8")
    hud = facts_prove.inspect_hud(html)
    assert hud["ok"] is True, hud["missing"]
    assert 'id="memoryPanel"' in html
    assert "Memory nav paints this panel" in html
    assert 'focus === "memory"' in html
    assert "scrollIntoView" in html
    assert 'id="ticketsScale"' in html
    assert "data-build=" in html
    css = (UI.parent / "crew.css").read_text(encoding="utf-8")
    assert ".block--focus" in css
    assert "#memoryPanel" in css


def test_http_list_save_search_export_survives_clear_and_paints_hud(client) -> None:
    """Operator Memory path: save, list, search, export, clear. facts.md stays."""
    space = client.http.post("/crew/spaces", json={"title": "CREW-8020-FACTS"}).json()
    sid = space["id"]
    saved = client.http.post(
        f"/crew/spaces/{sid}/memory",
        json={
            "name": "crew-port",
            "description": "which port crew listens on",
            "body": "8020",
        },
    ).json()
    assert saved["ok"] is True
    assert saved["file"] == "facts.md"

    listed = client.http.get(f"/crew/spaces/{sid}/memory").json()
    assert listed["file"] == "facts.md"
    assert listed["facts"][0]["name"] == "crew-port"
    assert listed["facts"][0]["body"] == "8020"

    searched = client.http.get(
        f"/crew/spaces/{sid}/memory/search", params={"q": "port"}
    ).json()
    assert searched["facts"][0]["body"] == "8020"

    exported = client.http.get(f"/crew/spaces/{sid}/memory/export").json()
    assert exported["filename"] == "facts.md"
    assert exported["markdown"].startswith("# Crew facts")
    assert "8020" in exported["markdown"]

    client.http.post(f"/crew/spaces/{sid}/messages", json={"text": "throw away"})
    _wait_idle(client, sid)
    cleared = client.http.post(f"/crew/spaces/{sid}/clear").json()
    assert cleared["transcript_cleared"] is True
    assert cleared["facts_survived"] is True
    assert cleared["memory"]["file"] == "facts.md"
    assert cleared["memory"]["facts"][0]["body"] == "8020"

    msgs = client.http.get(f"/crew/spaces/{sid}/messages").json()
    assert not any(m["role"] == "user" for m in msgs)
    after = client.http.get(f"/crew/spaces/{sid}/memory").json()
    assert after["facts"][0]["body"] == "8020"
    facts_path = (
        client.crew.settings.data_dir / "spaces" / sid / "memory" / "facts.md"
    )
    assert "8020" in facts_path.read_text(encoding="utf-8")

    page = client.http.get("/")
    assert page.status_code == 200
    hud = facts_prove.inspect_hud(page.text)
    assert hud["ok"] is True, hud["missing"]
    assert "cleared.facts_survived" in page.text


def test_get_facts_prove_is_display_only_and_not_live_green(client) -> None:
    body = client.http.get("/crew/facts-prove").json()
    assert body["ok"] is True
    assert body["status"] == facts_prove.STATUS_HUD
    assert body["host_up"] is False
    assert body["mutate"] is False
    assert body["live_8020_ci"] is False
    assert body["live_host_complete"] is False
    assert body["complete"] is False
    assert body["release"] is False
    assert body["kills_8020"] is False
    assert body["founder_restart_required"] is True
    assert body["hud"]["ok"] is True
    assert body["file"] == "facts.md"
    assert "NOT_PROVEN" in body["live_probe"] or "fail-closed" in body["live_probe"]
    assert body["issue_212_complete"] is False


def test_get_facts_prove_live_fail_closed_when_probes_off(client) -> None:
    """conftest sets CREW_LIVE_PROBES=0. Host-down/unprobed is NOT_PROVEN, not green."""
    body = client.http.get("/crew/facts-prove/live").json()
    assert body["ok"] is False
    assert body["status"] == facts_prove.STATUS_NOT_PROVEN
    assert body["host_up"] is False
    assert body["live_8020_ci"] is False
    assert body["live_host_complete"] is False
    assert body["complete"] is False
    assert body["kills_8020"] is False
    assert body["mutate"] is False
    assert "CREW_LIVE_PROBES=0" in body["detail"]
    assert "kill" not in (body["detail"] or "").lower()


def test_probe_live_closed_port_is_not_proven(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CREW_LIVE_PROBES", "1")
    report = facts_prove.probe_live("http://127.0.0.1:9", timeout=0.2)
    assert report["ok"] is False
    assert report["status"] == facts_prove.STATUS_NOT_PROVEN
    assert report["host_up"] is False
    assert report["live_8020_ci"] is False
    assert report["live_host_complete"] is False
    assert report["kills_8020"] is False
    assert "founder restart" in report["detail"]


def test_probe_live_reachable_hud_is_not_ci_green(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    html = UI.read_text(encoding="utf-8")
    monkeypatch.setenv("CREW_LIVE_PROBES", "1")

    def getter(url: str, timeout: float) -> tuple[int, str, str]:
        if url.rstrip("/").endswith("/crew/health"):
            return 200, '{"ok": true}', "application/json"
        return 200, html, "text/html"

    report = facts_prove.probe_live(
        "http://127.0.0.1:8020", timeout=0.2, get=getter
    )
    assert report["host_up"] is True
    assert report["status"] == facts_prove.STATUS_REACHABLE
    assert report["ok"] is True
    assert report["hud"]["ok"] is True
    assert report["build_scale"]["tickets_scale"] is True
    assert report["build_scale"]["build_button"] is True
    assert report["live_8020_ci"] is False
    assert report["live_host_complete"] is False
    assert report["complete"] is False
    assert report["release"] is False
    assert "not GitHub CI" in report["detail"]


def test_probe_live_host_up_without_memory_chrome_is_hud_miss(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CREW_LIVE_PROBES", "1")

    def getter(url: str, timeout: float) -> tuple[int, str, str]:
        return 200, "<html>no memory panel</html>", "text/html"

    report = facts_prove.probe_live("http://127.0.0.1:8020", get=getter)
    assert report["host_up"] is True
    assert report["ok"] is False
    assert report["status"] == facts_prove.STATUS_HUD_MISS
    assert report["live_8020_ci"] is False
    assert 'id="memoryPanel"' in report["hud"]["missing"]


def test_cli_host_down_exits_not_proven(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("CREW_LIVE_PROBES", "1")
    code = facts_prove.main(["http://127.0.0.1:9"])
    assert code == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == facts_prove.STATUS_NOT_PROVEN
    assert payload["live_8020_ci"] is False
    assert payload["live_host_complete"] is False


def test_module_does_not_kill_or_start_8020() -> None:
    source = PROVE_PY.read_text(encoding="utf-8")
    assert "os.kill" not in source
    assert "SIGTERM" not in source
    assert "subprocess" not in source
    assert "uvicorn" not in source
    assert "method=\"POST\"" not in source
    assert "method='POST'" not in source
    assert 'method="POST"' not in source
    assert "Request(url, method=\"GET\")" in source or 'method="GET"' in source
    assert "R-0015" in source
    assert "duckdb" not in source
    assert "packs." not in source
    assert "n8n" in source  # honesty stamp key, not a paste
    assert "from n8n" not in source
    assert "letta" not in source.lower()
    assert "memgpt" in source.lower()
    assert "import memgpt" not in source.lower()
