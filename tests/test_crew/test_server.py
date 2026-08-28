"""HTTP surface: health, spaces, roles, a scripted turn. No live model."""

from __future__ import annotations

import time
from collections.abc import Iterator
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from CortexOS.crew.llm import LLMResult
from CortexOS.crew.server import create_app
from tests.test_crew.conftest import FakeLLM


@pytest.fixture()
def client(settings, crew_env) -> Iterator[SimpleNamespace]:
    fake = FakeLLM()
    app = create_app(settings, llm_chat=fake)
    with TestClient(app) as tc:
        yield SimpleNamespace(http=tc, llm=fake, app=app, crew=app.state.crew)


def test_health_and_roles_and_spaces(client) -> None:
    health = client.http.get("/crew/health").json()
    assert health["ok"] is True
    assert health["provider"]["label"] == "explicit"
    assert health["computer_control"] is False
    assert health["grok_offloaded"] is True
    assert health["grok_autostart"] is False
    assert health["openvault"]["ok"] is False
    roles = client.http.get("/crew/roles").json()
    names = {r["name"] for r in roles}
    assert {"Ticket", "PRD", "Epic", "Gate", "Watchdog"} <= names
    assert all(r.get("kind") == "capability" for r in roles)
    detected = client.http.get("/crew/detect", params={"q": "write a PRD"}).json()
    assert detected["spawn"] is True
    assert any(c["name"] == "PRD" for c in detected["capabilities"])
    pong = client.http.get(
        "/crew/detect",
        params={"q": "Reply with exactly the word pong and do not spawn agents."},
    ).json()
    assert pong["spawn"] is False
    assert pong["pattern"] == "single_agent"
    plugs = client.http.get("/crew/connectors").json()
    assert any(p["slug"] == "openvault" for p in plugs)
    imported = client.http.post(
        "/crew/import", json={"title": "Dump", "text": "# user\nhi\n# assistant\nhey"}
    ).json()
    assert imported["count"] == 2
    msgs = client.http.get(f"/crew/spaces/{imported['space']['id']}/messages").json()
    assert msgs[-1]["content"] == "hey"
    created = client.http.post("/crew/spaces", json={"title": "HQ"}).json()
    assert created["title"] == "HQ"
    listed = client.http.get("/crew/spaces").json()
    assert created["id"] in {s["id"] for s in listed}
    assert imported["space"]["id"] in {s["id"] for s in listed}


def test_post_message_returns_run_and_writes_assistant(client) -> None:
    client.llm.manager.append(LLMResult(text="Ready."))
    space = client.http.post("/crew/spaces", json={"title": "HQ"}).json()
    posted = client.http.post(
        f"/crew/spaces/{space['id']}/messages", json={"text": "hello"}
    ).json()
    assert "run_id" in posted
    deadline = time.time() + 5
    while client.crew.runtime._space_run.get(space["id"]):
        if time.time() > deadline:
            raise AssertionError("run did not finish")
        time.sleep(0.02)
    msgs = client.http.get(f"/crew/spaces/{space['id']}/messages").json()
    assert [m["role"] for m in msgs] == ["user", "assistant"]
    assert msgs[-1]["content"] == "Ready."


def test_ui_index_is_served(client) -> None:
    page = client.http.get("/")
    assert page.status_code == 200
    assert "Cortex Crew" in page.text
    assert "Providers / API keys" in page.text
    assert "stolen.css" in page.text
    assert "Hide rail" in page.text
    assert "Hide inspector" in page.text
    assert "Plugins" in page.text
    assert "Drop chats" in page.text
    assert "drop-veil" in page.text
    assert "Import chats" not in page.text
    assert "Import mail" not in page.text
    assert "Ctrl K" in page.text
    assert "Where should bots run?" in page.text
    assert "This computer" in page.text
    assert "Teach" in page.text
    assert "Skills / tone" in page.text
    assert "Auto-detect" in page.text
    assert "Spawn the " not in page.text
    assert "Ask the Manager. Auto-detect who to spawn." in page.text
    assert "Login or 2FA. Take over this computer." in page.text
    assert "Done, I logged in" in page.text
    assert "class=\"takeover\"" in page.text or 'class="takeover"' in page.text
    assert "role-chip--hit" in page.text or "Templates, not a roster" in page.text
    assert "Draft skill" in page.text
    assert "Tickets" in page.text
    assert "Voice" in page.text
    assert "matchMedia" in page.text
    css = client.http.get("/stolen.css")
    assert css.status_code == 200
    assert b"--rail-w" in css.content
    assert b"inspector--closed" in css.content
    assert b"role-chip--hit" in css.content
    assert b"pointer-events: none" in css.content
    assert b"drop-veil" in css.content
    desk = client.http.get("/crew/desk").json()
    assert desk["ok"] is True
    assert "auto-merge" in desk["law"]
    assert desk["cursor"]["model"] == "grok-4.6"
    assert desk["prs"]["prs"] == []


def test_keys_get_never_returns_secrets(client) -> None:
    body = client.http.get("/crew/keys").json()
    assert "ANTHROPIC_API_KEY" in {f["key"] for f in body["fields"]}
    for field in body["fields"]:
        assert set(field) <= {"key", "label", "hint"}
    for flag in body["status"].values():
        assert set(flag) == {"configured"}
    assert body["status"]["CREW_MODEL"]["configured"] is True


def test_keys_post_activates_provider(client) -> None:
    from CortexOS.crew import config
    from CortexOS.crew.keys import KNOWN, save, status

    saved = client.http.post(
        "/crew/keys",
        json={"keys": {"CREW_MODEL": "", "OPENROUTER_API_KEY": "sk-or-test-not-real"}},
    ).json()
    assert saved["ok"] is True
    assert saved["active"]["label"] == "openrouter"
    assert saved["status"]["OPENROUTER_API_KEY"]["configured"] is True
    assert "sk-or-test-not-real" not in str(saved)
    assert status()["fields"]["OPENROUTER_API_KEY"]["configured"] is True
    active = next(p for p in config.resolve_providers() if p.active)
    assert active.label == "openrouter"
    save(client.crew.settings.data_dir, {k: "" for k in KNOWN})


def test_computer_control_arm_is_refused(client) -> None:
    resp = client.http.post("/crew/mcp/uacc/arm", json={"armed": True})
    assert resp.status_code == 403
    assert "computer control is off" in resp.json()["detail"] or "CORTEX_COMPUTER_CONTROL" in resp.json()["detail"]
    host = client.http.post("/crew/computer", json={"host": "this-pc"})
    assert host.status_code == 403
    mail = client.http.post(
        "/crew/import/mail",
        json={"title": "x.eml", "text": "From: a@b\nSubject: Gate fail\n\nCI red on main."},
    ).json()
    assert mail["ok"] is True
    assert "Gate fail" in mail["subject"]
    hits = client.http.get("/crew/search?q=Gate").json()
    assert any(h["kind"] in {"space", "message"} for h in hits)


def test_tickets_skills_and_voice(client, tmp_path, monkeypatch) -> None:
    claims = tmp_path / "CLAIMS.json"
    claims.write_text(
        '{"tickets":[{"ticket":"FF-03","role":"UNSEATED","may_write":false,"owner_pr":"x#41"}]}',
        encoding="utf-8",
    )
    monkeypatch.setenv("CREW_CLAIMS", str(claims))
    monkeypatch.setenv("CREW_RUNTIME", str(tmp_path / "RUNTIME.md"))
    board = client.http.get("/crew/tickets").json()
    assert board["ok"] is True
    assert board["unseated"] == 1
    assert board["tickets"][0]["ticket"] == "FF-03"
    assert "cloud agent" in board["law"]
    saved = client.http.post(
        "/crew/skills", json={"title": "monday-briefing", "body": "Goal:\nDo not spawn a cloud swarm.\n"}
    ).json()
    assert saved["ok"] is True
    skills = client.http.get("/crew/skills").json()
    assert any(s["title"] == "monday-briefing" for s in skills)
    voice = client.http.get("/crew/voice").json()
    assert voice["available"] is False
    assert "fake speech" in voice["reason"]
