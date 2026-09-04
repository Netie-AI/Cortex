"""HTTP surface: health, spaces, roles, a scripted turn. No live model."""

from __future__ import annotations

import asyncio
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
    assert "/crew.css" in page.text
    assert 'href="/stolen.css"' not in page.text
    assert "Hide spaces" in page.text
    assert "Hide belt" in page.text
    assert "Plugins" in page.text
    assert "Drop chats" in page.text
    assert "drop-veil" in page.text
    assert "Import chats" not in page.text
    assert "Import mail" not in page.text
    assert "Ctrl K" in page.text
    assert "Where should bots run?" in page.text
    assert "This computer" in page.text
    assert "Teach" in page.text
    assert "Collections" in page.text
    assert "HITL lease" in page.text
    assert "Heartbeat / wakes" in page.text
    assert "Needs you" in page.text
    assert "Crew owns leases" in page.text
    assert "Control display-only" in page.text
    assert "Recall payloads are untrusted" in page.text
    assert "Close GitHub issue" in page.text
    assert "Keep open" in page.text
    assert "Crew does not merge PRs" in page.text
    assert "facts.md" in page.text
    assert 'id="memSave"' in page.text
    assert 'id="memExport"' in page.text
    assert "/crew/spaces/" in page.text
    assert "Standing approvals" in page.text
    assert "one-off" in page.text
    assert "circuit-breaker" in page.text
    assert "Does not kill Manager" in page.text
    assert "Claim" in page.text
    assert "Release" in page.text
    assert "Control does not assign" in page.text
    assert "open GitHub" in page.text
    assert 'id="tabWork"' in page.text
    assert 'data-scope="space"' in page.text
    assert 'data-scope="user"' in page.text
    assert 'data-scope="agent"' in page.text
    assert 'data-scope="run"' in page.text
    assert "class=\"orb\"" in page.text or "class='orb'" in page.text or "class=\"orb " in page.text or "empty--orb" in page.text
    assert "Tickets" in page.text
    assert "Auto-detect" in page.text
    assert "Spawn the " not in page.text
    assert "Spawn teammate" in page.text
    assert "Clear chat" in page.text
    assert "active / idle / waiting / goal" in page.text
    assert "Ask the Manager. Type / for commands, @ for a teammate." in page.text
    assert 'id="suggest"' in page.text
    assert "/crew/commands" in page.text
    assert "composer__suggest" in page.text
    assert "Login or 2FA. Take over this computer." in page.text
    assert "Done, I logged in" in page.text
    assert "class=\"takeover\"" in page.text or 'class="takeover"' in page.text
    assert "role-chip--hit" in page.text or "Templates, not a roster" in page.text
    assert "Draft skill" in page.text
    assert "Tickets" in page.text
    assert "Voice" in page.text
    assert "matchMedia" in page.text
    assert "/v1/belt" in page.text
    assert "/crew/spaces/" in page.text
    assert "/threads" in page.text
    assert "switchboard" in page.text
    assert "thread--dead" in page.text
    assert "thread--waiting" in page.text
    assert 'id="a2aPending"' in page.text
    stolen = client.http.get("/stolen.css")
    assert stolen.status_code == 410
    css = client.http.get("/crew.css")
    assert css.status_code == 200
    assert b"--spaces-w" in css.content
    assert b"belt--shut" in css.content
    assert b"needs-you" in css.content
    assert b"role-chip--hit" in css.content
    assert b"pointer-events: none" in css.content
    assert b"drop-veil" in css.content
    assert b".orb" in css.content
    assert b"scope-chip" in css.content
    assert b"composer__suggest" in css.content
    assert b".thread" in css.content
    assert b"thread--dead" in css.content
    assert b"thread--waiting" in css.content
    assert b"--rail-ground" not in css.content
    assert b"--rk-page" not in css.content
    desk = client.http.get("/crew/desk").json()
    assert desk["ok"] is True
    assert "auto-merge" in desk["law"]
    assert desk["cursor"]["model"] == "grok-4.6"
    assert desk["prs"]["prs"] == []
    assert "usage" in desk
    assert desk["usage"]["llm_calls"] == 0
    assert "id=\"usageChip\"" in page.text
    assert "id=\"routePick\"" in page.text
    usage = client.http.get("/crew/usage").json()
    assert usage["llm_calls"] == 0
    assert "tokens" in usage


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


def test_pin_and_per_turn_refuse_without_fallback(client) -> None:
    pinned = client.http.post("/crew/providers", json={"provider": "anthropic"}).json()
    assert pinned["ok"] is False
    assert pinned["refused"]
    assert "no silent fallback" in pinned["refused"]
    space = client.http.post("/crew/spaces", json={"title": "HQ"}).json()
    posted = client.http.post(
        f"/crew/spaces/{space['id']}/messages",
        json={"text": "hello", "provider": "anthropic"},
    ).json()
    assert "error" in posted
    assert "no silent fallback" in posted["error"]
    msgs = client.http.get(f"/crew/spaces/{space['id']}/messages").json()
    assert msgs[-1]["role"] == "system"
    assert "anthropic" in msgs[-1]["content"]
    client.http.post("/crew/providers", json={"provider": ""})


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


def test_belt_and_wakes_are_get_only_and_skip_cortex_ping(client) -> None:
    """Control probes these. POST stays 405. Belt does not ping Cortex."""
    wakes = client.http.get("/crew/wakes").json()
    assert wakes["ok"] is True
    assert wakes["wakes"] == []
    belt = client.http.get("/v1/belt").json()
    alt = client.http.get("/crew/belt").json()
    assert belt == alt
    assert belt["bus"] == "github-issues"
    assert belt["converse"] is True
    assert belt["cortex"] == {"ok": False, "detail": "not probed"}
    assert belt["plan_for_next"]["decides_work_shape"] is False
    assert belt["wakes"] == []
    assert belt["queue"] == {"pending": 0, "leased": 0, "done": 0, "dead": 0}
    assert "tickets" in belt and "items" in belt["tickets"]
    assert belt["assignments"] == []
    assert "Control does not assign" in belt["assign_owner"]
    assert client.http.post("/crew/wakes", json={"kind": "timer"}).status_code == 405
    assert client.http.post("/v1/belt", json={"wake": "x"}).status_code == 405
    assert client.http.post("/crew/belt", json={"ticket": "x"}).status_code == 405
    client.crew.wakes.arm("timer", "morning brief")
    client.crew.queue.enqueue("ticket", {"ticket": "Cortex#97"})
    armed = client.http.get("/crew/wakes").json()
    assert armed["wakes"][0]["note"] == "morning brief"
    live = client.http.get("/v1/belt").json()
    assert live["wakes"][0]["note"] == "morning brief"
    assert live["queue"]["pending"] == 1


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
    assert board["assignments"] == []
    assert board["issues"] == []
    assert board["issues_ok"] is False
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


def test_ticket_claim_is_local_assign_and_refuses_seated(
    client, tmp_path, monkeypatch
) -> None:
    claims = tmp_path / "CLAIMS.json"
    claims.write_text(
        '{"tickets":[{"ticket":"Netie-AI/Cortex#128","owner_pr":"Netie-AI/Cortex#128","role":"SEATED"}]}',
        encoding="utf-8",
    )
    monkeypatch.setenv("CREW_CLAIMS", str(claims))
    space = client.http.post("/crew/spaces", json={"title": "HQ"}).json()
    seated = client.http.post(
        "/crew/tickets/claim",
        json={
            "spec": "Netie-AI/Cortex#128",
            "space_id": space["id"],
            "name": "Scout",
        },
    )
    assert seated.status_code == 409
    assert "SEATED" in seated.json()["detail"]
    blocked = client.http.post(
        "/crew/tickets/release",
        json={
            "spec": "Netie-AI/Cortex#128",
            "space_id": space["id"],
            "name": "Scout",
        },
    )
    assert blocked.status_code == 409
    claims.write_text('{"tickets":[]}', encoding="utf-8")
    ok = client.http.post(
        "/crew/tickets/claim",
        json={
            "spec": "Netie-AI/Cortex#162",
            "space_id": space["id"],
            "name": "Scout",
        },
    ).json()
    assert ok["ok"] is True
    assert "CLAIMS" in ok["law"]
    board = client.http.get("/crew/tickets").json()
    assert board["assignments"][0]["spec"] == "Netie-AI/Cortex#162"
    assert board["assignments"][0]["agent"] == "Scout"
    belt = client.http.get("/v1/belt").json()
    assert belt["assignments"][0]["agent"] == "Scout"
    dropped = client.http.post(
        "/crew/tickets/release",
        json={
            "spec": "Netie-AI/Cortex#162",
            "space_id": space["id"],
            "name": "Scout",
        },
    ).json()
    assert dropped["ok"] is True
    assert client.http.get("/crew/tickets").json()["assignments"] == []


def test_tickets_lists_fetched_github_issues_minus_claims(
    client, tmp_path, monkeypatch
) -> None:
    from CortexOS.crew import github as github_mod

    claims = tmp_path / "CLAIMS.json"
    claims.write_text(
        '{"tickets":[{"ticket":"Netie-AI/Cortex#128","owner_pr":"Netie-AI/Cortex#128","role":"SEATED"}]}',
        encoding="utf-8",
    )
    monkeypatch.setenv("CREW_CLAIMS", str(claims))
    monkeypatch.setattr(
        github_mod,
        "list_open_issues",
        lambda **_k: {
            "ok": True,
            "detail": "",
            "issues": [
                {
                    "spec": "Netie-AI/Cortex#128",
                    "title": "seated",
                    "seated": True,
                    "ready": False,
                },
                {
                    "spec": "Netie-AI/Cortex#164",
                    "title": "fetch hud",
                    "seated": False,
                    "ready": True,
                },
            ],
        },
    )
    board = client.http.get("/crew/tickets").json()
    specs = [row["spec"] for row in board["issues"]]
    assert "Netie-AI/Cortex#128" not in specs
    assert board["issues"][0]["spec"] == "Netie-AI/Cortex#164"
    assert board["issues"][0]["ready"] is True
    assert board["issues_ok"] is True


def test_operator_can_choose_runtime_backend(client) -> None:
    health = client.http.get("/crew/health").json()
    runtime = health["runtime"]
    assert runtime["backend"] == "laptop"
    assert runtime["flag"] == "CREW_CF_COMPUTER"
    assert runtime["cf_computer"] is False
    assert runtime["production"] is False
    assert runtime["preview"] is True
    assert "token" not in runtime
    listed = client.http.get("/crew/runtime").json()
    assert listed == runtime
    switched = client.http.post("/crew/runtime", json={"backend": "cloudflare-computer"}).json()
    assert switched["ok"] is True
    assert switched["backend"] == "cloudflare-computer"
    assert switched["production"] is False
    assert client.http.get("/crew/runtime").json()["backend"] == "cloudflare-computer"
    back = client.http.post("/crew/runtime", json={"backend": "laptop"}).json()
    assert back["backend"] == "laptop"
    bad = client.http.post("/crew/runtime", json={"backend": "firecracker"})
    assert bad.status_code == 400


def test_commands_autocomplete_and_mention_targets(client) -> None:
    body = client.http.get("/crew/commands").json()
    slashes = {c["slash"] for c in body["commands"]}
    assert {"desk", "board", "estate", "ship_gate", "spawn", "kill", "done"} <= slashes
    assert any(c["kind"] == "skill" and c["slash"] == "build" for c in body["commands"])
    assert any(c["kind"] == "routine" for c in body["commands"])
    names = {m["name"] for m in body["mentions"]}
    assert "Manager" in names
    assert "PRD" in names
    desk = client.http.get("/crew/commands", params={"q": "/desk"}).json()
    assert desk["commands"][0]["action"] == "desk_status"
    space = client.http.post("/crew/spaces", json={"title": "Mentions"}).json()
    posted = client.http.post(
        f"/crew/spaces/{space['id']}/messages", json={"text": "/desk"}
    ).json()
    assert posted.get("run_id") is None
    assert posted.get("command") == "desk"
    msgs = client.http.get(f"/crew/spaces/{space['id']}/messages").json()
    assert [m["role"] for m in msgs] == ["user", "tool"]
    assert msgs[0]["meta"]["a2a"]["from"] == "operator"
    assert msgs[0]["meta"]["a2a"]["to"] == "Manager"
    assert msgs[1]["meta"]["tool"] == "desk_status"
    role_hit = client.http.post(
        f"/crew/spaces/{space['id']}/messages", json={"text": "@PRD draft it"}
    ).json()
    assert "run_id" in role_hit
    lined = client.http.get(f"/crew/spaces/{space['id']}/messages").json()
    users = [m for m in lined if m["role"] == "user"]
    assert users[-1]["meta"]["a2a"]["to"] == "PRD"
    assert users[-1]["meta"]["mentions"][0]["kind"] == "role"


def test_operator_life_routes_spawn_kill_clear(client) -> None:
    space = client.http.post("/crew/spaces", json={"title": "Life"}).json()
    spawned = client.http.post(
        f"/crew/spaces/{space['id']}/agents",
        json={"name": "Scout", "brief": "hold the line", "mode": "goal", "goal": "hold the line"},
    ).json()
    assert spawned["ok"] is True
    scout = spawned["agent"]
    assert scout["mode"] == "goal"
    assert scout["status"] == "goal"
    assert scout["goal_text"] == "hold the line"
    listed = client.http.get(f"/crew/spaces/{space['id']}/agents").json()
    assert any(a["name"] == "Scout" and a["status"] == "goal" for a in listed)

    client.http.post(
        f"/crew/spaces/{space['id']}/messages",
        json={"text": "throw away"},
    )
    # The scripted model is empty; wait until the space is idle so clear
    # does not race a finishing run.
    deadline = time.time() + 5
    while client.crew.runtime._space_run.get(space["id"]):
        if time.time() > deadline:
            break
        time.sleep(0.02)
    cleared = client.http.post(f"/crew/spaces/{space['id']}/clear").json()
    assert cleared["ok"] is True
    msgs = client.http.get(f"/crew/spaces/{space['id']}/messages").json()
    assert not any(m["role"] == "user" for m in msgs)
    after = client.http.get(f"/crew/spaces/{space['id']}/agents").json()
    keeper = next(a for a in after if a["name"] == "Scout")
    assert keeper["mode"] == "goal"
    assert keeper["goal_text"] == "hold the line"
    assert keeper["status"] == "goal"

    saved = client.http.post(
        f"/crew/spaces/{space['id']}/memory",
        json={
            "name": "crew-port",
            "description": "which port crew listens on",
            "body": "8020",
        },
    ).json()
    assert saved["ok"] is True
    listed_mem = client.http.get(f"/crew/spaces/{space['id']}/memory").json()
    assert listed_mem["file"] == "facts.md"
    assert listed_mem["facts"][0]["name"] == "crew-port"
    client.http.post(
        f"/crew/spaces/{space['id']}/messages",
        json={"text": "throw away"},
    )
    deadline = time.time() + 5
    while client.crew.runtime._space_run.get(space["id"]):
        if time.time() > deadline:
            break
        time.sleep(0.02)
    client.http.post(f"/crew/spaces/{space['id']}/clear")
    after_clear = client.http.get(f"/crew/spaces/{space['id']}/memory").json()
    assert after_clear["facts"][0]["body"] == "8020"
    exported = client.http.get(f"/crew/spaces/{space['id']}/memory/export").json()
    assert exported["filename"] == "facts.md"
    assert "# Crew facts" in exported["markdown"]
    assert "8020" in exported["markdown"]
    page = client.http.get("/")
    assert "facts.md" in page.text
    assert 'id="memory"' in page.text

    killed = client.http.post(
        f"/crew/spaces/{space['id']}/agents/{scout['id']}/kill",
        json={"reason": "operator killed"},
    ).json()
    assert killed["ok"] is True
    assert killed["agent"]["status"] == "stopped"
    assert "operator killed" in killed["agent"]["stop_reason"]
    refuse = client.http.post(
        f"/crew/spaces/{space['id']}/agents/{scout['id']}/accept",
        json={"brief": "nope"},
    )
    assert refuse.status_code == 400
    assert "DENIED" in refuse.json()["detail"]


def test_threads_hud_paints_pending_and_dead_on_switchboard(client) -> None:
    """GET /spaces/{id}/threads is a view over messages + live asks, not a second bus."""
    from CortexOS.crew import a2a

    space = client.http.post("/crew/spaces", json={"title": "Wire"}).json()
    missing = client.http.get("/crew/spaces/no-such-space/threads")
    assert missing.status_code == 404
    crew = client.crew
    mgr = crew.runtime.ensure_manager(space["id"])
    scout = crew.store.upsert_agent(space["id"], "Scout", role_prompt="scout the brief.")
    ask = crew.store.add_message(
        space["id"],
        "agent",
        "what did you find?",
        agent_id=mgr["id"],
        to_agent_id=scout["id"],
        meta={"a2a": {"kind": a2a.ASK, "from": "Manager", "to": "Scout", "reply_to": None}},
    )

    async def _arm() -> None:
        crew.runtime.switch.open_ask(mgr["id"], scout["id"], ask["id"])

    asyncio.run(_arm())
    body = client.http.get(f"/crew/spaces/{space['id']}/threads").json()
    assert body["bus"] == "switchboard"
    assert body["pending"] == 1
    assert body["threads"][0]["id"] == ask["id"]
    assert body["threads"][0]["status"] == a2a.WAITING
    assert body["asks"][0]["from"] == "Manager"
    assert body["asks"][0]["to"] == "Scout"

    crew.store.add_message(
        space["id"],
        "system",
        "Manager was waiting on Scout, which stopped running.",
        agent_id=scout["id"],
        to_agent_id=mgr["id"],
        meta={
            "a2a": {
                "kind": a2a.NO_ANSWER,
                "from": "Scout",
                "to": "Manager",
                "reply_to": ask["id"],
                "status": a2a.DEAD,
            }
        },
    )
    crew.runtime.switch.abandon(scout["id"], "Scout stopped running")
    dead = client.http.get(f"/crew/spaces/{space['id']}/threads").json()
    assert dead["pending"] == 0
    assert dead["threads"][0]["status"] == a2a.DEAD
    tape = client.http.get(f"/crew/spaces/{space['id']}/messages").json()
    assert tape[0]["meta"]["a2a"]["kind"] == a2a.ASK
    assert tape[-1]["meta"]["a2a"]["status"] == a2a.DEAD
