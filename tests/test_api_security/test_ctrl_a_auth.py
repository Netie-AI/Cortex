"""T2-CTRL-A (#263): routine, goal and race routes are role-gated through the engine auth port.

Every assertion is on what an HTTP caller gets back (status and body) and on
what the route would have touched. A spy wraps every engine function the
handlers call, so a refused request is proven to have reached no handler at
all, and the stored routines, goals, commitments, seeks, telemetry and
scoreboard are compared before and after. The no-false-positive tests show a
caller with a sufficient role gets the same status and body as the ungated
route (auth disabled), which is the pre-gate behaviour.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from fastapi.testclient import TestClient

from CortexOS.execution import (
    action_event,
    action_value,
    commitments,
    enterprise_goal,
    goal_audit,
    osr,
    race_router,
    routine_composer,
    routine_scheduler,
    scoreboard,
    seeker,
    workflow_store,
)
from CortexOS.security import auth_port

KEYS = {"viewer": "t2a-viewer-key", "steward": "t2a-steward-key", "admin": "t2a-admin-key"}
_KEY_ENV = ";".join(f"{role}:{key}" for role, key in KEYS.items())
_ROLE_ORDER = ("viewer", "steward", "admin")
_GATED_MODULES = {
    "CortexOS.api.routine_routes",
    "CortexOS.api.goal_routes",
    "CortexOS.api.race_routes",
}

# Every engine function a handler in the three route files calls. The spy
# forwards to the real function and records the call.
_SPIED: list[tuple[Any, str]] = [
    (routine_scheduler, name)
    for name in (
        "init",
        "list_routines",
        "global_budget_state",
        "create_from_goal",
        "update_routine",
        "tick",
        "pause_all",
        "resume_all",
        "get_routine",
        "delete_routine",
        "pause",
        "resume",
        "run_once",
        "list_runs",
    )
] + [
    (routine_composer, "compose"),
    (enterprise_goal, "list_goals"),
    (enterprise_goal, "create_goal"),
    (enterprise_goal, "get_goal"),
    (enterprise_goal, "update_goal"),
    (enterprise_goal, "delete_goal"),
    (enterprise_goal, "list_seeks"),
    (seeker, "seek"),
    (seeker, "record_proposal_outcome"),
    (commitments, "list_commitments"),
    (commitments, "record_from_text"),
    (commitments, "close"),
    (commitments, "dismiss"),
    (action_event, "summary"),
    (action_event, "list_events"),
    (action_event, "daily"),
    (action_event, "compact"),
    (action_value, "table"),
    (osr, "classify"),
    (osr, "classify_external"),
    (race_router, "auto_route"),
    (scoreboard, "init"),
    (scoreboard, "list_families"),
    (scoreboard, "family_stats"),
    (scoreboard, "best_preset"),
]


def _isolate(monkeypatch, tmp_path) -> None:
    for module, name in (
        (routine_scheduler, "routines.db"),
        (enterprise_goal, "goals.db"),
        (scoreboard, "scoreboard.db"),
        (osr, "osr.db"),
        (action_value, "action_value.db"),
        (action_event, "action_events.db"),
        (commitments, "commitments.db"),
        (workflow_store, "wf-runs.db"),
    ):
        monkeypatch.setattr(module, "DB_PATH", tmp_path / name)
    monkeypatch.setattr(goal_audit, "LEDGER_DB_PATH", tmp_path / "ledger.db")


def _install_spy(monkeypatch) -> list[str]:
    calls: list[str] = []
    for module, name in _SPIED:
        real = getattr(module, name)
        label = f"{module.__name__.rsplit('.', 1)[-1]}.{name}"

        if _is_coroutine(real):

            async def _async_spy(*a: Any, _real: Any = real, _label: str = label, **k: Any) -> Any:
                calls.append(_label)
                return await _real(*a, **k)

            monkeypatch.setattr(module, name, _async_spy)
        else:

            def _spy(*a: Any, _real: Any = real, _label: str = label, **k: Any) -> Any:
                calls.append(_label)
                return _real(*a, **k)

            monkeypatch.setattr(module, name, _spy)
    return calls


def _is_coroutine(fn: Any) -> bool:
    import inspect

    return inspect.iscoroutinefunction(fn)


def _seed() -> dict[str, str]:
    routine = routine_scheduler.create_from_goal("summarise the inbox every hour")
    goal = enterprise_goal.create_goal("Grow monthly recurring revenue ethically")
    assert goal["ok"] is True
    found = commitments.record_from_text(
        "I'll update the pricing page", source="chat", source_id="t2a"
    )
    assert found["ok"] is True and found["stored"]
    cid = commitments.list_commitments("open", 50)[0]["id"]
    return {"rid": routine["id"], "goal": goal["goal"]["id"], "cid": cid}


def _snapshot() -> str:
    """Everything a route in the three files can change, as one comparable string."""
    goals = enterprise_goal.list_goals()
    return json.dumps(
        {
            "routines": routine_scheduler.list_routines(),
            "runs": [routine_scheduler.list_runs(r["id"]) for r in routine_scheduler.list_routines()],
            "goals": goals,
            "seeks": [enterprise_goal.list_seeks(g["id"]) for g in goals],
            "commitments": [commitments.list_commitments(s, 500) for s in ("open", "closed", "dismissed")],
            "events": action_event.list_events(limit=500),
            "daily": action_event.daily(None),
            "scoreboard": scoreboard.list_families(),
        },
        sort_keys=True,
        default=str,
    )


@pytest.fixture
def env(monkeypatch, tmp_path):
    monkeypatch.setenv("PACK", "dms")
    monkeypatch.delenv("DMS_AUTH_DISABLED", raising=False)
    monkeypatch.setenv("DMS_API_KEYS", _KEY_ENV)
    _isolate(monkeypatch, tmp_path)
    routine_scheduler.init()
    scoreboard.init()
    ids = _seed()
    calls = _install_spy(monkeypatch)

    from CortexOS.api.app import create_app

    client = TestClient(create_app())
    return {"client": client, "tmp": tmp_path, "ids": ids, "calls": calls}


def _hdr(role: str | None) -> dict[str, str]:
    return {"X-API-Key": KEYS[role]} if role else {}


# (method, path template, json body or None, minimum role)
_ROUTES: list[tuple[str, str, Any, str]] = [
    # routine_routes.py
    ("GET", "/api/routines", None, "viewer"),
    ("POST", "/api/routines/draft", {"goal": "Draft release notes every Friday at 5pm"}, "viewer"),
    ("POST", "/api/routines", {"goal": "watch the inbox hourly"}, "steward"),
    ("POST", "/api/routines/tick", None, "admin"),
    ("POST", "/api/routines/pause-all", None, "steward"),
    ("POST", "/api/routines/resume-all", None, "admin"),
    ("GET", "/api/routines/{rid}", None, "viewer"),
    ("PATCH", "/api/routines/{rid}", {"interval_seconds": 60}, "steward"),
    ("DELETE", "/api/routines/{rid}", None, "admin"),
    ("POST", "/api/routines/{rid}/pause", {"reason": "user"}, "steward"),
    ("POST", "/api/routines/{rid}/resume", None, "steward"),
    ("POST", "/api/routines/{rid}/run", None, "steward"),
    ("POST", "/api/routines/{rid}/fire", {"external_text": "I'll call the supplier", "source": "webhook"}, "steward"),
    ("GET", "/api/routines/{rid}/runs", None, "viewer"),
    # goal_routes.py
    ("GET", "/api/goals", None, "viewer"),
    ("POST", "/api/goals", {"statement": "Cut stockouts by a third"}, "admin"),
    ("GET", "/api/goals/{goal}", None, "viewer"),
    ("PATCH", "/api/goals/{goal}", {"soft_preferences": {"autonomy_level": "act"}}, "admin"),
    ("DELETE", "/api/goals/{goal}", None, "admin"),
    ("GET", "/api/goals/{goal}/seeks", None, "viewer"),
    ("POST", "/api/goals/{goal}/outcome", {"proposal_id": "p1", "outcome": "dismissed"}, "steward"),
    ("GET", "/api/commitments", None, "viewer"),
    ("POST", "/api/commitments/scan", {"text": "I'll renew the licence", "source": "chat"}, "steward"),
    ("POST", "/api/commitments/{cid}/close", None, "steward"),
    ("POST", "/api/commitments/{cid}/dismiss", None, "steward"),
    ("GET", "/api/engine/telemetry", None, "viewer"),
    ("POST", "/api/engine/telemetry/compact", None, "admin"),
    ("GET", "/api/goals/{goal}/values", None, "viewer"),
    ("POST", "/api/engine/osr", {"text": "Grow monthly recurring revenue"}, "viewer"),
    ("POST", "/api/engine/seek", {"goal_id": "{goal}"}, "steward"),
    # race_routes.py
    ("POST", "/api/engine/auto", {"goal": "say hello", "min_runs": 1}, "steward"),
    ("GET", "/api/engine/scoreboard", None, "viewer"),
    ("GET", "/api/engine/scoreboard/{family}", None, "viewer"),
]


def _below(min_role: str) -> list[str]:
    return list(_ROLE_ORDER[: _ROLE_ORDER.index(min_role)])


def _at_or_above(min_role: str) -> list[str]:
    return list(_ROLE_ORDER[_ROLE_ORDER.index(min_role) :])


def _fill(value: Any, ids: dict[str, str]) -> Any:
    if isinstance(value, str):
        return value.format(family="chat", **ids)
    if isinstance(value, dict):
        return {k: _fill(v, ids) for k, v in value.items()}
    return value


def _call(client: TestClient, method: str, path: str, body: Any, ids: dict[str, str], role: str | None):
    kwargs: dict[str, Any] = {"headers": _hdr(role)}
    if body is not None:
        kwargs["json"] = _fill(body, ids)
    return client.request(method, _fill(path, ids), **kwargs)


def _assert_no_path(text: str, tmp_path) -> None:
    assert str(tmp_path) not in text
    assert "/home/" not in text and "/tmp/" not in text and ":\\\\" not in text


# --- refusals: 401 without a key, 403 below the role, no side effect ----------


def test_every_route_refuses_without_key_or_with_low_role_and_touches_nothing(env):
    client, ids, calls, tmp = env["client"], env["ids"], env["calls"], env["tmp"]
    before = _snapshot()
    calls.clear()

    for method, path, body, min_role in _ROUTES:
        res = _call(client, method, path, body, ids, None)
        assert res.status_code == 401, (method, path, res.status_code, res.text)
        assert res.json() == {"detail": "Valid API key required (X-API-Key or Bearer)"}
        _assert_no_path(res.text, tmp)
        for role in _below(min_role):
            res = _call(client, method, path, body, ids, role)
            assert res.status_code == 403, (method, path, role, res.status_code, res.text)
            assert res.json()["detail"].startswith(f"Requires role {min_role!r}")
            _assert_no_path(res.text, tmp)

    assert calls == [], "a refused request reached a handler"
    assert _snapshot() == before


def test_schema_invalid_body_without_key_is_401_not_422(env):
    """The gate answers before body validation, so a keyless caller learns nothing about the schema."""
    client, ids = env["client"], env["ids"]
    for method, path, body in (
        ("POST", "/api/routines/draft", {}),
        ("POST", "/api/routines/{rid}/fire", {}),
        ("POST", "/api/commitments/scan", {"text": ""}),
        ("POST", "/api/goals/{goal}/outcome", {}),
        ("POST", "/api/engine/osr", {}),
        ("POST", "/api/engine/auto", {"min_runs": "many"}),
    ):
        res = client.request(method, _fill(path, ids), json=body)
        assert res.status_code == 401, (method, path, res.status_code)
    assert env["calls"] == []


def test_every_route_in_the_three_files_is_gated(env):
    """Walks the live app, so a route added later without the gate fails here."""
    client, ids = env["client"], env["ids"]
    seen: set[tuple[str, str]] = set()
    for route in client.app.routes:
        endpoint = getattr(route, "endpoint", None)
        if getattr(endpoint, "__module__", "") not in _GATED_MODULES:
            continue
        concrete = (
            route.path.replace("{rid}", ids["rid"])
            .replace("{goal_id}", ids["goal"])
            .replace("{cid}", ids["cid"])
            .replace("{family}", "chat")
        )
        for method in sorted(route.methods):
            seen.add((method, route.path))
            res = client.request(method, concrete, json={})
            assert res.status_code == 401, (method, route.path, res.status_code)
    listed = {(m, p.replace("{goal}", "{goal_id}")) for m, p, _, _ in _ROUTES}
    assert seen == listed, "route table in this test is out of date"
    assert env["calls"] == []


def test_no_authorizer_refuses_every_route_even_for_admin(env, monkeypatch):
    client, ids, calls = env["client"], env["ids"], env["calls"]
    saved = auth_port.registered_authorizer()
    before = _snapshot()
    calls.clear()
    auth_port.clear_authorizer()
    monkeypatch.setattr(auth_port, "_load_active_pack", lambda: None)
    try:
        for method, path, body, _ in _ROUTES:
            res = _call(client, method, path, body, ids, "admin")
            assert res.status_code == 503, (method, path, res.status_code)
            assert res.json() == {"detail": auth_port.NO_AUTHORIZER_DETAIL}
    finally:
        if saved is not None:
            auth_port.register_authorizer(saved)
    assert calls == []
    assert _snapshot() == before


# --- no false positive: a sufficient role sees today's behaviour --------------


def _normalise(value: Any) -> Any:
    """Drop values that differ between two otherwise identical calls (ids, clocks)."""
    volatile = ("id", "ts", "at", "time", "ms", "created", "updated", "next", "last", "latency", "run", "fingerprint")
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            key = str(k).lower()
            if any(key == t or key.endswith("_" + t) or key.startswith(t + "_") for t in volatile):
                out[k] = "<v>"
            else:
                out[k] = _normalise(v)
        return out
    if isinstance(value, list):
        return [_normalise(v) for v in value]
    return value


def _fresh(monkeypatch, tmp_path, name: str) -> tuple[TestClient, dict[str, str]]:
    root = tmp_path / name
    root.mkdir()
    _isolate(monkeypatch, root)
    routine_scheduler.init()
    scoreboard.init()
    ids = _seed()
    from CortexOS.api.app import create_app

    return TestClient(create_app()), ids


@pytest.mark.parametrize("route", _ROUTES, ids=[f"{m} {p}" for m, p, _, _ in _ROUTES])
def test_sufficient_role_gets_the_ungated_response(route, monkeypatch, tmp_path):
    """Each role at or above the minimum gets the status and body the route gave with no gate."""
    method, path, body, min_role = route
    monkeypatch.setenv("PACK", "dms")
    monkeypatch.setenv("DMS_API_KEYS", _KEY_ENV)

    monkeypatch.setenv("DMS_AUTH_DISABLED", "1")
    client, ids = _fresh(monkeypatch, tmp_path, "ungated")
    want = _call(client, method, path, body, ids, None)
    want_state = _normalise(json.loads(_snapshot()))
    monkeypatch.delenv("DMS_AUTH_DISABLED")

    assert want.status_code < 500, (method, path, want.status_code, want.text)
    for role in _at_or_above(min_role):
        client, ids = _fresh(monkeypatch, tmp_path, f"gated-{role}")
        got = _call(client, method, path, body, ids, role)
        assert got.status_code == want.status_code, (method, path, role, got.text)
        assert _normalise(got.json()) == _normalise(want.json()), (method, path, role)
        assert _normalise(json.loads(_snapshot())) == want_state, (method, path, role)


def test_steward_routine_lifecycle_and_admin_goal_write_work(env):
    """User-visible effects of the everyday flows, end to end, with the lowest role that may do them."""
    client, ids = env["client"], env["ids"]
    st, ad, vw = _hdr("steward"), _hdr("admin"), _hdr("viewer")

    created = client.post("/api/routines", json={"goal": "watch the inbox hourly"}, headers=st)
    assert created.status_code == 200
    rid = created.json()["routine"]["id"]
    assert rid in [r["id"] for r in client.get("/api/routines", headers=vw).json()["routines"]]

    run = client.post(f"/api/routines/{rid}/run", headers=st)
    assert run.status_code == 200 and run.json()["ok"] is True
    assert client.get(f"/api/routines/{rid}/runs", headers=vw).json()["runs"]

    paused = client.post(f"/api/routines/{rid}/pause", json={"reason": "user"}, headers=st)
    assert paused.status_code == 200 and paused.json()["routine"]["status"] == "paused"
    stopped = client.post("/api/routines/pause-all", headers=st)
    assert stopped.status_code == 200

    fired = client.post(
        f"/api/routines/{ids['rid']}/fire",
        json={"external_text": "I'll send the invoice by friday", "source": "webhook"},
        headers=st,
    )
    assert fired.status_code == 200 and fired.json()["wrapped"] is True
    open_now = client.get("/api/commitments", headers=vw).json()["commitments"]
    assert any("invoice" in c["snippet"] for c in open_now)

    goal = client.post("/api/goals", json={"statement": "Cut stockouts by a third"}, headers=ad)
    assert goal.status_code == 200
    gid = goal.json()["goal"]["id"]
    seek = client.post("/api/engine/seek", json={"goal_id": gid}, headers=st)
    assert seek.status_code == 200 and seek.json()["proposals"]
    assert client.get(f"/api/goals/{gid}/seeks", headers=vw).json()["seeks"]

    closed = client.post(f"/api/commitments/{ids['cid']}/close", headers=st)
    assert closed.status_code == 200 and closed.json()["commitment"]["status"] == "closed"

    deleted = client.delete(f"/api/routines/{rid}", headers=ad)
    assert deleted.status_code == 200 and deleted.json() == {"ok": True}
    assert client.get(f"/api/routines/{rid}", headers=vw).status_code == 404


def test_bearer_header_and_auth_disabled_still_work(env, monkeypatch):
    client = env["client"]
    res = client.get("/api/routines", headers={"Authorization": f"Bearer {KEYS['viewer']}"})
    assert res.status_code == 200 and res.json()["ok"] is True
    monkeypatch.setenv("DMS_AUTH_DISABLED", "1")
    res = client.post("/api/routines", json={"goal": "watch the inbox hourly"})
    assert res.status_code == 200 and res.json()["ok"] is True
