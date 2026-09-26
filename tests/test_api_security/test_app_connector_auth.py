"""TRUST-01 (#257): /api/apps and /api/connectors are role-gated through the engine auth port.

Every assertion is on what an HTTP caller gets back (status and body) and on
the stored state the route would have changed (the app store, the agent inbox,
the chat store, the app runner). The no-false-positive tests compare a
sufficient-role caller's response with the response the same route gives with
auth disabled, which is the pre-gate behaviour.
"""

from __future__ import annotations

import base64
import io
import json
import zipfile
from typing import Any

import pytest
from fastapi.testclient import TestClient

from CortexOS.connectors import agents, cursor_session
from CortexOS.execution import app_runner, app_store
from CortexOS.security import auth_port

KEYS = {"viewer": "t01-viewer-key", "steward": "t01-steward-key", "admin": "t01-admin-key"}
_KEY_ENV = ";".join(f"{role}:{key}" for role, key in KEYS.items())


def _zip_b64() -> str:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        zf.writestr("main.py", "print('hi')")
        zf.writestr("requirements.txt", "fastapi\n")
    return base64.b64encode(buffer.getvalue()).decode()


@pytest.fixture
def env(monkeypatch, tmp_path):
    monkeypatch.setenv("PACK", "dms")
    monkeypatch.delenv("DMS_AUTH_DISABLED", raising=False)
    monkeypatch.setenv("DMS_API_KEYS", _KEY_ENV)
    monkeypatch.delenv("CORTEX_COMPUTER_CONTROL", raising=False)
    monkeypatch.delenv("CORTEX_COMPUTER_CONTROL_EXECUTE", raising=False)
    monkeypatch.setattr(app_store, "DB_PATH", tmp_path / "apps.db")
    monkeypatch.setattr(app_store, "APPS_ROOT", tmp_path / "apps")
    app_store.init()
    cursor_session.reset_for_tests(tmp_path / "chats.json")
    agents.reset_for_tests()

    runner_calls: list[dict[str, Any]] = []

    def _spy_start(**kwargs: Any) -> dict[str, Any]:
        runner_calls.append(kwargs)
        return {"ok": False, "error": "spy_refused_to_spawn"}

    monkeypatch.setattr(app_runner, "start", _spy_start)

    from CortexOS.api.app import create_app

    with TestClient(create_app()) as client:
        yield {"client": client, "tmp": tmp_path, "runner_calls": runner_calls}
    cursor_session.reset_for_tests()
    agents.reset_for_tests()


def _hdr(role: str | None) -> dict[str, str]:
    return {"X-API-Key": KEYS[role]} if role else {}


def _folder(tmp_path) -> str:
    root = tmp_path / "proj"
    root.mkdir(exist_ok=True)
    (root / "main.py").write_text("print('hi')\n", encoding="utf-8")
    (root / "requirements.txt").write_text("fastapi\n", encoding="utf-8")
    return str(root)


def _draft_id() -> str:
    out = app_store.import_zip_bytes(base64.b64decode(_zip_b64()), name="t01-draft")
    assert out["ok"] is True
    return str(out["app"]["id"])


def _approved_id() -> str:
    app_id = _draft_id()
    assert app_store.approve(app_id)["ok"] is True
    return app_id


def _app_snapshot() -> list[tuple[Any, ...]]:
    return sorted(
        (a["id"], a["status"], a.get("run_status"), a.get("pid")) for a in app_store.list_apps()
    )


def _assert_no_path(body: str, tmp_path) -> None:
    assert str(tmp_path) not in body
    assert "/home/" not in body and "/tmp/" not in body and ":\\\\" not in body


# (method, path template, json body factory, minimum role)
def _app_routes(tmp_path) -> list[tuple[str, str, Any, str]]:
    return [
        ("POST", "/api/apps/import", lambda: {"zip_base64": _zip_b64()}, "steward"),
        ("POST", "/api/apps/import-folder", lambda: {"path": _folder(tmp_path)}, "admin"),
        ("GET", "/api/apps", None, "viewer"),
        ("GET", "/api/apps/{draft}", None, "viewer"),
        ("POST", "/api/apps/{draft}/approve", None, "admin"),
        ("POST", "/api/apps/{draft}/reject", lambda: {"reason": "no"}, "steward"),
        ("POST", "/api/apps/{draft}/rescan", None, "steward"),
        ("POST", "/api/apps/{approved}/start", None, "admin"),
        ("POST", "/api/apps/{approved}/stop", None, "steward"),
        ("POST", "/api/apps/{draft}/dockerize", None, "steward"),
        ("DELETE", "/api/apps/{draft}", None, "admin"),
    ]


_CONNECTOR_ROUTES: list[tuple[str, str, Any, str]] = [
    # GET /api/connectors itself is the static desk shell, served without a key
    # (T2-FAILCLOSED, #263); tests/test_api_security/test_fail_closed.py covers it.
    ("GET", "/api/connectors/workspaces", None, "viewer"),
    ("GET", "/api/connectors/agents", None, "viewer"),
    ("GET", "/api/connectors/agents/constructor/messages", None, "viewer"),
    ("POST", "/api/connectors/agents/constructor/messages", lambda: {"text": "t01", "kind": "task"}, "steward"),
    ("GET", "/api/connectors/computer-control", None, "viewer"),
    ("POST", "/api/connectors/computer-control/invoke", lambda: {"action": "click", "x": 1, "y": 1}, "admin"),
    ("POST", "/api/connectors/dispatch", lambda: {"text": "ship it", "kind": "task"}, "steward"),
    ("GET", "/api/connectors/cursor/chats", None, "viewer"),
    ("POST", "/api/connectors/cursor/chats", lambda: {"workspace": "dms", "task": "t01"}, "steward"),
    ("GET", "/api/connectors/cursor/chats/c1/messages", None, "viewer"),
    ("POST", "/api/connectors/cursor/chats/c1/instruct", lambda: {"instruction": "go"}, "steward"),
]

_ROLE_ORDER = ("viewer", "steward", "admin")


def _below(min_role: str) -> list[str]:
    return list(_ROLE_ORDER[: _ROLE_ORDER.index(min_role)])


def _call(client: TestClient, method: str, path: str, body: Any, role: str | None):
    kwargs: dict[str, Any] = {"headers": _hdr(role)}
    if body is not None:
        kwargs["json"] = body()
    return client.request(method, path, **kwargs)


def _connector_state() -> tuple[str, str]:
    inbox = json.dumps({a["id"]: agents.messages(a["id"]) for a in agents.roster()}, sort_keys=True)
    chats = json.dumps(cursor_session.get_port().list_chats(), sort_keys=True, default=str)
    return inbox, chats


# --- refusals: 401 without a key, 403 below the role, no side effect ----------


def test_app_routes_refuse_without_key_or_with_low_role_and_change_nothing(env):
    client, tmp = env["client"], env["tmp"]
    ids = {"draft": _draft_id(), "approved": _approved_id()}
    before = _app_snapshot()
    draft_dir = app_store.get_app(ids["draft"])["dir"]

    for method, template, body, min_role in _app_routes(tmp):
        path = template.format(**ids)
        res = _call(client, method, path, body, None)
        assert res.status_code == 401, (method, path, res.status_code, res.text)
        _assert_no_path(res.text, tmp)
        for role in _below(min_role):
            res = _call(client, method, path, body, role)
            assert res.status_code == 403, (method, path, role, res.status_code, res.text)
            _assert_no_path(res.text, tmp)

    assert _app_snapshot() == before
    assert env["runner_calls"] == []
    from pathlib import Path

    assert not (Path(draft_dir) / "Dockerfile").exists()


def test_bad_import_body_without_key_is_401_not_400(env):
    res = env["client"].post("/api/apps/import", json={"zip_base64": "@@not-base64@@"})
    assert res.status_code == 401


def test_connector_routes_refuse_without_key_or_with_low_role_and_change_nothing(env):
    client, tmp = env["client"], env["tmp"]
    before = _connector_state()
    for method, path, body, min_role in _CONNECTOR_ROUTES:
        res = _call(client, method, path, body, None)
        assert res.status_code == 401, (method, path, res.status_code, res.text)
        _assert_no_path(res.text, tmp)
        for role in _below(min_role):
            res = _call(client, method, path, body, role)
            assert res.status_code == 403, (method, path, role, res.status_code, res.text)
            _assert_no_path(res.text, tmp)
    assert _connector_state() == before


# The one ungated path: the operator desk's static HTML shell (no data, no
# paths). Exempted by exact method and path, never by prefix.
_DESK_SHELL = ("GET", "/api/connectors")


def test_every_registered_app_and_connector_route_is_gated(env):
    """A route added later without the gate fails here, not in production."""
    client = env["client"]
    seen = 0
    for path, ops in client.app.openapi()["paths"].items():
        if not (path.startswith("/api/apps") or path.startswith("/api/connectors")):
            continue
        concrete = path.replace("{app_id}", "x").replace("{agent_id}", "constructor").replace(
            "{chat_id}", "c1"
        )
        for method in sorted(m.upper() for m in ops):
            if (method, path) == _DESK_SHELL:
                continue
            seen += 1
            res = client.request(method, concrete, json={})
            assert res.status_code == 401, (method, path, res.status_code)
    assert seen >= len(_app_routes(env["tmp"])) + len(_CONNECTOR_ROUTES)


# --- no false positive: a sufficient role sees today's behaviour --------------


def _baseline(monkeypatch, fn):
    """Run ``fn`` with auth disabled: the response the route gave before the gate."""
    monkeypatch.setenv("DMS_AUTH_DISABLED", "1")
    try:
        return fn()
    finally:
        monkeypatch.delenv("DMS_AUTH_DISABLED", raising=False)


def _drop_volatile(value: Any) -> Any:
    # The built-in app row refreshes ``updated_at`` on every read, gate or no gate.
    if isinstance(value, dict):
        return {k: _drop_volatile(v) for k, v in value.items() if k != "updated_at"}
    if isinstance(value, list):
        return [_drop_volatile(v) for v in value]
    return value


def _stable(res: Any) -> Any:
    if "application/json" in res.headers.get("content-type", ""):
        return _drop_volatile(res.json())
    return res.text


def test_viewer_reads_match_ungated_response(env, monkeypatch):
    client = env["client"]
    app_id = _draft_id()
    for path in (
        "/api/apps",
        f"/api/apps/{app_id}",
        "/api/apps/missing",
        "/api/connectors/workspaces",
        "/api/connectors/agents",
        "/api/connectors/agents/constructor/messages",
        "/api/connectors/computer-control",
        "/api/connectors/cursor/chats",
        "/api/connectors",
    ):
        want = _baseline(monkeypatch, lambda p=path: client.get(p))
        for role in _ROLE_ORDER:
            got = client.get(path, headers=_hdr(role))
            assert got.status_code == want.status_code, (path, role)
            assert _stable(got) == _stable(want), (path, role)


def test_steward_and_admin_mutations_work(env):
    client, tmp = env["client"], env["tmp"]

    imported = client.post("/api/apps/import", json={"zip_base64": _zip_b64()}, headers=_hdr("steward"))
    assert imported.status_code == 200
    assert imported.json()["app"]["status"] == "draft"
    app_id = imported.json()["app"]["id"]
    assert [a["id"] for a in client.get("/api/apps", headers=_hdr("viewer")).json()["apps"]].count(app_id) == 1

    docked = client.post(f"/api/apps/{app_id}/dockerize", headers=_hdr("steward"))
    assert docked.status_code == 200 and docked.json()["created"] is True

    approved = client.post(f"/api/apps/{app_id}/approve", headers=_hdr("admin"))
    assert approved.status_code == 200 and approved.json()["app"]["status"] == "approved"

    started = client.post(f"/api/apps/{app_id}/start", headers=_hdr("admin"))
    assert started.status_code == 409  # the spy runner refuses to spawn
    assert len(env["runner_calls"]) == 1  # but the admin did reach the runner

    rejected_id = client.post(
        "/api/apps/import", json={"zip_base64": _zip_b64()}, headers=_hdr("steward")
    ).json()["app"]["id"]
    rej = client.post(f"/api/apps/{rejected_id}/reject", json={"reason": "no"}, headers=_hdr("steward"))
    assert rej.status_code == 200 and rej.json()["app"]["status"] == "rejected"

    folder = client.post("/api/apps/import-folder", json={"path": _folder(tmp)}, headers=_hdr("admin"))
    assert folder.status_code == 200 and folder.json()["app"]["status"] == "draft"

    deleted = client.delete(f"/api/apps/{app_id}", headers=_hdr("admin"))
    assert deleted.status_code == 200 and deleted.json() == {"ok": True}
    assert client.get(f"/api/apps/{app_id}", headers=_hdr("viewer")).status_code == 404

    posted = client.post(
        "/api/connectors/agents/constructor/messages",
        json={"text": "build the desk", "kind": "task"},
        headers=_hdr("steward"),
    )
    assert posted.status_code == 200
    hist = client.get("/api/connectors/agents/constructor/messages", headers=_hdr("viewer")).json()
    assert "build the desk" in [m["text"] for m in hist["messages"]]

    sent = client.post(
        "/api/connectors/dispatch", json={"text": "hello there", "kind": "chat"}, headers=_hdr("steward")
    )
    assert sent.status_code == 200 and sent.json()["cursor_chat_id"] is None

    # Admin reaches the computer-control handler, which itself still refuses (fail-closed probe).
    click = client.post(
        "/api/connectors/computer-control/invoke", json={"action": "click", "x": 1, "y": 1}, headers=_hdr("admin")
    )
    assert click.status_code == 403
    assert click.json()["detail"]["executed"] is False


def test_bearer_header_is_accepted(env):
    res = env["client"].get("/api/apps", headers={"Authorization": f"Bearer {KEYS['viewer']}"})
    assert res.status_code == 200 and res.json()["ok"] is True


def test_auth_disabled_still_lets_local_dev_through(env, monkeypatch):
    monkeypatch.setenv("DMS_AUTH_DISABLED", "1")
    res = env["client"].post("/api/apps/import", json={"zip_base64": _zip_b64()})
    assert res.status_code == 200 and res.json()["app"]["status"] == "draft"


# --- the port itself fails closed ---------------------------------------------


@pytest.fixture
def swap_authorizer():
    saved = auth_port.registered_authorizer()
    yield
    if saved is not None:
        auth_port.register_authorizer(saved)
    else:
        auth_port.clear_authorizer()


def test_no_registered_authorizer_refuses_even_an_admin(env, monkeypatch, swap_authorizer):
    client = env["client"]
    before = _app_snapshot()
    auth_port.clear_authorizer()
    monkeypatch.setattr(auth_port, "_load_active_pack", lambda: None)
    for headers in (_hdr("admin"), {}):
        res = client.post("/api/apps/import", json={"zip_base64": _zip_b64()}, headers=headers)
        assert res.status_code == 503
        assert res.json() == {"detail": auth_port.NO_AUTHORIZER_DETAIL}
        assert client.get("/api/connectors/workspaces", headers=headers).status_code == 503
    monkeypatch.setenv("DMS_AUTH_DISABLED", "1")
    assert client.get("/api/apps").status_code == 503
    assert _app_snapshot() == before


def test_authorizer_error_refuses_without_leaking_text(env, swap_authorizer):
    class Broken:
        async def authorize(self, request, min_role):
            raise RuntimeError(f"boom at {env['tmp']}")

    auth_port.register_authorizer(Broken())
    res = env["client"].get("/api/apps", headers=_hdr("admin"))
    assert res.status_code == 503
    assert res.json() == {"detail": auth_port.AUTHORIZER_FAILED_DETAIL}


def test_engine_rechecks_the_role_an_authorizer_returns(env, swap_authorizer):
    class TooGenerous:
        async def authorize(self, request, min_role):
            class P:
                role = "viewer"
                actor = "x"

            return P()

    auth_port.register_authorizer(TooGenerous())
    before = _app_snapshot()
    res = env["client"].post("/api/apps/import", json={"zip_base64": _zip_b64()}, headers=_hdr("admin"))
    assert res.status_code == 403
    assert _app_snapshot() == before


def test_unknown_role_is_rejected_at_declaration():
    with pytest.raises(ValueError):
        auth_port.require_role("root")


def test_dms_pack_registers_its_authorizer():
    import packs.dms.security.api_auth as api_auth

    api_auth.register_request_authorizer()
    assert isinstance(auth_port.registered_authorizer(), api_auth.DmsRequestAuthorizer)
