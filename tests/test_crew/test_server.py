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


def test_peer_health_wait_is_one_second() -> None:
    """Control's belt probe is 1.5s. Crew peer pings must return in 1s."""
    from CortexOS.crew.server import PEER_HEALTH_WAIT_S

    assert PEER_HEALTH_WAIT_S == 1.0


def test_health_and_belt_fail_closed_when_engine_hangs(client) -> None:
    """Control probes Crew in 1.5s. A hung Cortex ping must not stall health or belt."""
    import asyncio

    from CortexOS.crew.server import PEER_HEALTH_WAIT_S

    assert PEER_HEALTH_WAIT_S == 1.0
    n = {"n": 0}

    async def hang() -> dict:
        n["n"] += 1
        await asyncio.sleep(8)
        return {"ok": True, "url": "http://127.0.0.1:9"}

    client.crew.bridge.health = hang  # type: ignore[method-assign]
    t0 = time.time()
    health = client.http.get("/crew/health").json()
    assert time.time() - t0 < 2.5
    assert health["ok"] is True
    assert health["engine"]["ok"] is False
    assert n["n"] == 1
    t1 = time.time()
    belt = client.http.get("/crew/belt").json()
    assert time.time() - t1 < 0.5
    assert n["n"] == 1
    assert belt["ok"] is True
    assert belt["cortex"]["ok"] is False
    assert belt["cortex"]["detail"] == "not probed"
    v1 = client.http.get("/v1/belt").json()
    assert v1["cortex"]["ok"] is False
    assert v1["cortex"]["detail"] == "not probed"
    assert client.http.post("/v1/belt", json={"ticket": "x"}).status_code != 200


def test_health_pings_openvault_once(client, monkeypatch: pytest.MonkeyPatch) -> None:
    """resolve_providers must not healthz; health() already probes OV once."""
    monkeypatch.setenv("CREW_OPENVAULT", "1")
    n = {"n": 0}

    def slow(timeout: float = 1.5) -> dict:
        n["n"] += 1
        time.sleep(0.35)
        return {"ok": False, "detail": "hung"}

    async def fast_engine() -> dict:
        return {"ok": False, "detail": "offline"}

    monkeypatch.setattr("CortexOS.crew.openvault.healthz", slow)
    client.crew.bridge.health = fast_engine  # type: ignore[method-assign]
    t0 = time.perf_counter()
    health = client.http.get("/crew/health").json()
    elapsed = time.perf_counter() - t0
    assert n["n"] == 1
    assert elapsed < 0.6
    assert health["openvault"]["ok"] is False


def test_health_and_roles_and_spaces(client) -> None:
    health = client.http.get("/crew/health").json()
    assert health["ok"] is True
    assert health["provider"]["label"] == "explicit"
    assert health["computer_control"] is False
    assert health["grok_offloaded"] is True
    assert health["grok_autostart"] is False
    assert health["openvault"]["ok"] is False
    assert isinstance(health.get("wake_tick_at"), (int, float))
    roles = client.http.get("/crew/roles").json()
    names = {r["name"] for r in roles}
    assert {"Ticket", "PRD", "Epic", "Gate", "Watchdog"} <= names
    assert all(r.get("kind") == "capability" for r in roles)
    detected = client.http.get("/crew/detect", params={"q": "write a PRD"}).json()
    assert detected["spawn"] is True
    assert any(c["name"] == "PRD" for c in detected["capabilities"])
    assert detected["load_skill_now"] is False
    mail = client.http.get("/crew/detect", params={"q": "draft outreach for a factory"}).json()
    assert mail["load_skill_now"] is True
    assert "outreach" in mail["skills"]
    waves = client.http.get(
        "/crew/detect",
        params={"q": "write the PRD then tickets then gate this against invariants"},
    ).json()
    assert "wave 0" in waves["wave_plan"]
    assert "nothing was spawned" in waves["wave_plan"]
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


def test_providers_register_wizard_is_paste_only(client) -> None:
    body = client.http.get("/crew/providers").json()
    assert body["engine_url"] == "http://127.0.0.1:9"
    assert body["prefer"] == "auto"
    assert body["vault_listing"] == "skipped"
    ids = {row["id"] for row in body["register"]}
    assert {"cursor", "anthropic", "nvidia", "ollama"} <= ids
    assert all(row["auto_register"] is False for row in body["register"])
    assert "get key" in client.http.get("/").text
    assert "does not create accounts" in client.http.get("/").text


def test_providers_skips_live_vault_listing(client, monkeypatch: pytest.MonkeyPatch) -> None:
    """Hung OpenVault must not stall GET /crew/providers. Health already pings OV."""
    monkeypatch.setenv("CREW_OPENVAULT", "1")
    n = {"n": 0}

    def hang(timeout: float = 1.2) -> frozenset:
        n["n"] += 1
        time.sleep(3)
        return frozenset({"cursor"})

    monkeypatch.setattr("CortexOS.crew.openvault.vaulted_provider_ids", hang)
    t0 = time.perf_counter()
    body = client.http.get("/crew/providers").json()
    elapsed = time.perf_counter() - t0
    assert n["n"] == 0
    assert elapsed < 0.5
    assert body["vault_listing"] == "skipped"
    assert body["prefer"] == "auto"
    assert all(row["auto_register"] is False for row in body["register"])


def test_belt_and_timer_wake(client) -> None:
    from datetime import datetime, timedelta, timezone

    space = client.http.post("/crew/spaces", json={"title": "Belt"}).json()
    future = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()
    created = client.http.post(
        "/crew/wakes",
        json={"space_id": space["id"], "fire_at": future, "note": "morning brief"},
    )
    assert created.status_code == 200, created.text
    catalog = client.http.post(
        "/crew/wakes",
        json={"space_id": space["id"], "fire_at": future, "note": "catalog"},
    )
    assert catalog.status_code == 200, catalog.text
    assert "cortex_ask" in catalog.json()["wake"]["note"]
    assert "metrics" in catalog.json()["wake"]["note"]
    confirm = client.crew.store.create_confirm(
        space["id"], run_id=None, agent_id=None, tool="win.Type", args={"text": "hi"}
    )
    client.crew.runtime.work_queue.push(
        space["id"], {"kind": "user", "text": "later"}, item_id="belt-q1"
    )
    belt = client.http.get("/crew/belt").json()
    assert belt["ok"] is True
    assert belt["converse"] is True
    assert belt["plan_for_next"]["decides_work_shape"] is False
    assert space["id"] in {s["id"] for s in belt["spaces"]}
    assert any(w["note"] == "morning brief" for w in belt["wakes"])
    assert confirm["id"] in {c["id"] for c in belt["confirms"]}
    assert belt["queue"]["pending"] >= 1
    listed = client.http.get("/crew/wakes").json()
    assert listed["ok"] is True
    assert any(w["space_id"] == space["id"] for w in listed["wakes"])
    v1 = client.http.get("/v1/belt").json()
    assert v1["bus"] == "github-issues"
    assert any(w["note"] == "morning brief" for w in v1["wakes"])
    assert v1["queue"] == belt["queue"]
    assert v1["confirms"] == belt["confirms"]
    assert client.http.post("/v1/belt", json={"ticket": "x"}).status_code != 200
    missing = client.http.post(
        "/crew/wakes",
        json={"space_id": "no-such-space", "fire_at": future, "note": "nope"},
    )
    assert missing.status_code == 404
    ev = client.http.post(
        "/crew/wakes/event",
        json={"space_id": space["id"], "event_key": "hitl", "note": "after confirm"},
    )
    assert ev.status_code == 200, ev.text
    assert ev.json()["wake"]["event_key"] == "hitl"
    fired = client.http.post("/crew/wakes/fire", json={"event_key": "hitl"})
    assert fired.status_code == 200, fired.text
    assert fired.json()["ok"] is True
    missing_ev = client.http.post(
        "/crew/wakes/event",
        json={"space_id": "no-such-space", "event_key": "hitl"},
    )
    assert missing_ev.status_code == 404


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
    assert "crew.css" in page.text
    assert "const apiSend" in page.text
    assert 'apiSend("/crew/keys"' in page.text
    assert 'apiSend("/crew/computer"' in page.text
    assert 'api("/crew/spaces/" + state.spaceId + "/messages"' in page.text
    assert 'apiSend("/crew/spaces/" + state.spaceId + "/messages"' not in page.text
    assert 'apiGet("/crew/mcp")' in page.text
    assert 'state.mcp = await api("/crew/mcp")' not in page.text
    assert 'state.skills = await api("/crew/skills")' not in page.text
    assert 'state.skills = await apiGet("/crew/skills")' in page.text
    assert "stolen.css" not in page.text
    assert "apiGet" in page.text
    assert "AbortController" in page.text
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
    assert "Plan" in page.text
    assert "local chat" in page.text
    assert "Auto-detect" in page.text
    assert "Spawn the " not in page.text
    assert "Ask the Manager. /skillname invokes a skill. Click a specialist to brief them." in page.text
    assert "Login or 2FA. Take over this computer." in page.text
    assert "Done, I logged in" in page.text
    assert "class=\"takeover\"" in page.text or 'class="takeover"' in page.text
    assert "role-chip--hit" in page.text or "Templates, not a roster" in page.text
    assert "Draft skill" in page.text
    assert "Tickets" in page.text
    assert "Wakes" in page.text
    assert "Queue" in page.text
    assert "renderEngine" in page.text
    assert "engineChip" in page.text
    assert "rail--pinned" in page.text
    assert "inspector--pinned" in page.text
    assert "Control does not start Cortex." in page.text
    assert "Control does not POST wakes" in page.text
    assert "skills unread" in page.text
    assert "tickets unread" in page.text
    assert "desk unread" in page.text
    assert "routines unread" in page.text
    assert "roles unread" in page.text
    assert "providers unread" in page.text
    assert 'apiGet("/crew/providers").catch(() => ({ active: null, chain: [], register: [] }))' not in page.text
    assert "GET /crew/providers failed." in page.text
    assert "vault unread" in page.text
    assert "vault_listing" in page.text
    assert "no provider configured" in page.text
    assert "agents unread" in page.text
    assert "todos unread" in page.text
    assert "confirms unread" in page.text
    assert "spaces unread" in page.text
    assert "keys unread" in page.text
    assert "!fields.length" in page.text
    assert "computer unread. GET /crew/computer not yet." in page.text
    assert "computer unread. GET /crew/computer failed." in page.text
    assert "computer unread. GET /crew/mcp not yet." not in page.text
    assert "mcp unread. GET /crew/mcp not yet." in page.text
    assert "import unread. GET /crew/import not yet." in page.text
    assert "import unread. GET /crew/import failed." in page.text
    assert "detect unread. GET /crew/detect failed." in page.text
    assert 'apiGet("/crew/import")' in page.text
    assert 'apiGet("/crew/detect")' in page.text
    assert 'apiGet("/crew/computer")' in page.text
    assert "function paintHost" in page.text
    assert "search unread. GET /crew/search failed." in page.text
    assert "search none." in page.text
    assert "Type two characters." in page.text
    assert 'apiGet("/crew/search?q="' in page.text
    assert "class=\"chip empty\"" in page.text or 'class="chip empty"' in page.text
    assert "No results" not in page.text
    assert "function setConn" in page.text
    assert "not JSON" in page.text
    assert "r.json().catch(() => ({}))" not in page.text
    assert 'apiGet("/crew/spaces").catch(() => [])' not in page.text
    assert "Did not create General" in page.text
    assert "GET /crew/keys failed." in page.text
    assert "No agents yet." not in page.text
    assert "loading..." not in page.text
    assert "checking..." not in page.text
    assert "engine unread" in page.text
    assert "GET /crew/health not yet." in page.text
    assert "engine unread. GET /crew/health failed." in page.text
    assert 'apiGet("/crew/health").catch(() => ({ ok: false, unread: true }))' in page.text
    assert "voice unread" in page.text
    assert "voice unread. GET /crew/voice failed." in page.text
    assert "voice none. Crew will not fake speech." in page.text
    assert 'await apiGet("/crew/voice")' in page.text
    assert "graph none" in page.text
    assert "activity none" in page.text
    assert "transcript unread. GET messages not yet." in page.text
    assert "transcript unread. GET messages failed." in page.text
    assert "messages none." in page.text
    assert "Nothing here yet." not in page.text
    assert 'apiGet("/crew/spaces/" + id + "/messages")' in page.text
    assert 'apiGet("/crew/spaces/" + id + "/messages?after="' in page.text
    assert 'api("/crew/spaces/" + id + "/messages")' not in page.text
    assert "applySpaceBoard" in page.text
    assert "GET /crew/roles failed." in page.text
    assert 'id="conn"' in page.text
    assert ">unread</span>" in page.text
    assert page.text.count('<div class="composer">') == 1
    assert page.text.count("composer__input") == 1
    assert "Crew owns leases" in page.text
    assert "Voice" in page.text
    assert "matchMedia" in page.text
    css = client.http.get("/crew.css")
    assert css.status_code == 200
    assert b"--rail-w" in css.content
    assert b"inspector--closed" in css.content
    assert b"role-chip--hit" in css.content
    assert b"pointer-events: none" in css.content
    assert b"drop-veil" in css.content
    assert b"hover: hover" in css.content
    assert b"#engine.empty" in css.content
    assert b"#skills.empty" in css.content
    assert b"#roles.empty" in css.content
    assert b"#agents.empty" in css.content
    assert b"#confirms.empty" in css.content
    assert b"#spaces.empty" in css.content
    assert b"#providerChip.empty" in css.content
    assert b"#searchHits.empty" in css.content
    assert b"#hostStatus.empty" in css.content
    assert b"#log.empty" in css.content
    assert b"--note" in css.content
    gone = client.http.get("/stolen.css")
    assert gone.status_code == 410
    assert client.http.get("/favicon.ico").status_code == 204
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


def test_detect_idle_import_and_computer_get(client) -> None:
    idle = client.http.get("/crew/detect")
    assert idle.status_code == 200
    body = idle.json()
    assert body["pattern"] == "idle"
    assert body["spawn"] is False
    assert body["capabilities"] == []

    imp = client.http.get("/crew/import")
    assert imp.status_code == 200
    drop = imp.json()
    assert drop["ok"] is True
    assert drop["drop"] == "ready"
    assert drop["files_waiting"] == 0
    assert drop["detail"] == "import none."
    mail = client.http.get("/crew/import/mail")
    assert mail.status_code == 200
    assert mail.json()["ok"] is True
    listed = client.http.get("/crew/imports")
    assert listed.status_code == 200
    assert listed.json()["count"] == 0
    assert listed.json()["detail"] == "import none."

    host = client.http.get("/crew/computer")
    assert host.status_code == 200
    comp = host.json()
    assert comp["ok"] is True
    assert comp["master"] is False
    assert comp["armed"] is False
    assert comp["host"] == "off"
    assert "disarmed" in comp["detail"]


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
    assert voice["ok"] is False
    assert voice["available"] is False
    assert "fake speech" in voice["reason"]
    space = client.http.post("/crew/spaces", json={"title": "Plan"}).json()
    empty = client.http.get(f"/crew/spaces/{space['id']}/todos").json()
    assert empty == []
    missing = client.http.get("/crew/spaces/no-such/todos")
    assert missing.status_code == 404


def test_start_crew_script_will_not_double_bind() -> None:
    """YOU step 8 runs start_crew.ps1. It must not bind a second process on a hung :8020."""
    from pathlib import Path

    text = (Path(__file__).resolve().parents[2] / "scripts" / "start_crew.ps1").read_text(
        encoding="utf-8"
    )
    assert "function PortHeld" in text
    assert "R-0015" in text
    assert "YOU step 8" in text
    assert "not starting a second process" in text
    assert "PYTHONPATH" in text
