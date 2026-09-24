"""T2-RESP (#263): app and connector responses carry no host paths; archive import has limits.

Every assertion is on what an HTTP caller gets back (status and body) or on the
stored state a route would change (app store rows, files under the apps root,
the agent inbox, the chat store, the app runner). The role matrix re-proves the
TRUST-01 gate on every route in the two route files, with a spy on each side
effect, and proves that each sufficient role still reaches the handler.
"""

from __future__ import annotations

import base64
import io
import json
import re
import zipfile
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from CortexOS.connectors import agents, cursor_session
from CortexOS.execution import app_runner, app_store
from CortexOS.paths import repo_root

KEYS = {"viewer": "t2r-viewer-key", "steward": "t2r-steward-key", "admin": "t2r-admin-key"}
_KEY_ENV = ";".join(f"{role}:{key}" for role, key in KEYS.items())
_ROLES = ("viewer", "steward", "admin")
_SPAWN_ERROR_PATH = "/opt/t2r-secret/install/main.py"

# Anything that looks like an absolute host path. URL paths the API is meant to
# return (``/cortex/constructor/``, ``/api/...``) are not host paths, so they are
# checked by the known-root assertions instead of this pattern.
_HOSTISH = re.compile(r"(/home/|/tmp/|/root/|/opt/|/usr/|/var/|/srv/|(?<![A-Za-z])[A-Za-z]:(\\\\|/))")


def _zip(files: dict[str, str | bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, content in files.items():
            zf.writestr(name, content)
    return buffer.getvalue()


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


def _app_zip() -> bytes:
    return _zip({"main.py": "print('hi')\n", "requirements.txt": "fastapi\n"})


@pytest.fixture
def env(monkeypatch, tmp_path):
    monkeypatch.setenv("PACK", "dms")
    monkeypatch.delenv("DMS_AUTH_DISABLED", raising=False)
    monkeypatch.setenv("DMS_API_KEYS", _KEY_ENV)
    monkeypatch.delenv("CORTEX_COMPUTER_CONTROL", raising=False)
    monkeypatch.delenv("CORTEX_COMPUTER_CONTROL_EXECUTE", raising=False)
    ws_root = tmp_path / "ws-dms-root"
    ws_root.mkdir()
    monkeypatch.setenv("CORTEX_WS_DMS", str(ws_root))
    monkeypatch.setattr(app_store, "DB_PATH", tmp_path / "apps.db")
    monkeypatch.setattr(app_store, "APPS_ROOT", tmp_path / "apps")
    app_store.init()
    cursor_session.reset_for_tests(tmp_path / "chats.json")
    agents.reset_for_tests()

    runner_calls: list[dict[str, Any]] = []

    def _spy_start(**kwargs: Any) -> dict[str, Any]:
        runner_calls.append(kwargs)
        # A realistic spawn failure quotes an absolute path from the host.
        return {
            "ok": False,
            "error": f"start_spawn:[Errno 2] No such file or directory: '{_SPAWN_ERROR_PATH}'",
        }

    monkeypatch.setattr(app_runner, "start", _spy_start)

    from CortexOS.api.app import create_app

    with TestClient(create_app()) as client:
        yield {"client": client, "tmp": tmp_path, "ws_root": ws_root, "runner_calls": runner_calls}
    cursor_session.reset_for_tests()
    agents.reset_for_tests()


def _hdr(role: str | None) -> dict[str, str]:
    return {"X-API-Key": KEYS[role]} if role else {}


def _folder(tmp_path: Path) -> str:
    root = tmp_path / "proj"
    root.mkdir(exist_ok=True)
    (root / "main.py").write_text("print('hi')\n", encoding="utf-8")
    (root / "requirements.txt").write_text("fastapi\n", encoding="utf-8")
    return str(root)


def _draft_id() -> str:
    out = app_store.import_zip_bytes(_app_zip(), name="t2r-draft")
    assert out["ok"] is True
    return str(out["app"]["id"])


def _approved_id() -> str:
    app_id = _draft_id()
    assert app_store.approve(app_id)["ok"] is True
    return app_id


def _files_under(root: Path) -> list[str]:
    if not root.exists():
        return []
    return sorted(str(p.relative_to(root)) for p in root.rglob("*"))


def _app_state() -> tuple[Any, ...]:
    rows = sorted(
        (a["id"], a["status"], a.get("run_status"), a.get("pid"), a.get("rejected_reason"))
        for a in app_store.list_apps()
    )
    return tuple(rows), tuple(_files_under(app_store.APPS_ROOT))


def _connector_state() -> tuple[str, str]:
    inbox = json.dumps({a["id"]: agents.messages(a["id"]) for a in agents.roster()}, sort_keys=True)
    chats = json.dumps(cursor_session.get_port().list_chats(), sort_keys=True, default=str)
    return inbox, chats


def _host_roots(env: dict[str, Any]) -> list[str]:
    roots = {
        str(env["tmp"]),
        str(Path(env["tmp"]).resolve()),
        str(env["ws_root"]),
        str(app_store.APPS_ROOT),
        str(repo_root()),
        str(Path.home()),
        _SPAWN_ERROR_PATH,
    }
    return sorted(r for r in roots if len(r) > 1)


def _assert_no_host_path(text: str, env: dict[str, Any], where: Any) -> None:
    for root in _host_roots(env):
        assert root not in text, (where, root, text[:400])
    assert not _HOSTISH.search(text), (where, _HOSTISH.search(text), text[:400])


# (method, path template, json body factory, minimum role, status for that role)
def _app_routes(tmp_path: Path) -> list[tuple[str, str, Any, str, int]]:
    return [
        ("POST", "/api/apps/import", lambda: {"zip_base64": _b64(_app_zip())}, "steward", 200),
        ("POST", "/api/apps/import-folder", lambda: {"path": _folder(tmp_path)}, "admin", 200),
        ("GET", "/api/apps", None, "viewer", 200),
        ("GET", "/api/apps/{draft}", None, "viewer", 200),
        ("POST", "/api/apps/{draft}/approve", None, "admin", 200),
        ("POST", "/api/apps/{draft}/reject", lambda: {"reason": "no"}, "steward", 200),
        ("POST", "/api/apps/{draft}/rescan", None, "steward", 200),
        # The spy runner refuses to spawn, so an admin start is a 409 from the handler.
        ("POST", "/api/apps/{approved}/start", None, "admin", 409),
        ("POST", "/api/apps/{approved}/stop", None, "steward", 200),
        ("POST", "/api/apps/{draft}/dockerize", None, "steward", 200),
        ("DELETE", "/api/apps/{draft}", None, "admin", 200),
    ]


_CONNECTOR_ROUTES: list[tuple[str, str, Any, str, int]] = [
    ("GET", "/api/connectors", None, "viewer", 200),
    ("GET", "/api/connectors/workspaces", None, "viewer", 200),
    ("GET", "/api/connectors/agents", None, "viewer", 200),
    ("GET", "/api/connectors/agents/constructor/messages", None, "viewer", 200),
    ("POST", "/api/connectors/agents/constructor/messages", lambda: {"text": "t2r", "kind": "task"}, "steward", 200),
    ("GET", "/api/connectors/computer-control", None, "viewer", 200),
    # The fail-closed probe refuses an unarmed click from inside the handler.
    ("POST", "/api/connectors/computer-control/invoke", lambda: {"action": "click", "x": 1, "y": 1}, "admin", 403),
    ("POST", "/api/connectors/dispatch", lambda: {"text": "ship the dms slice", "kind": "task"}, "steward", 200),
    ("GET", "/api/connectors/cursor/chats", None, "viewer", 200),
    ("POST", "/api/connectors/cursor/chats", lambda: {"workspace": "dms", "task": "t2r"}, "steward", 200),
    ("GET", "/api/connectors/cursor/chats/{chat}/messages", None, "viewer", 200),
    ("POST", "/api/connectors/cursor/chats/{chat}/instruct", lambda: {"instruction": "go"}, "steward", 200),
]


def _call(client: TestClient, method: str, path: str, body: Any, role: str | None):
    kwargs: dict[str, Any] = {"headers": _hdr(role)}
    if body is not None:
        kwargs["json"] = body()
    return client.request(method, path, **kwargs)


def _allowed(role: str, min_role: str) -> bool:
    return _ROLES.index(role) >= _ROLES.index(min_role)


# --- role matrix: every route x (no key, viewer, steward, admin) ---------------


def test_app_route_matrix_refusals_change_nothing_and_sufficient_roles_reach_handler(env):
    client, tmp = env["client"], env["tmp"]
    for method, template, body, min_role, ok_status in _app_routes(tmp):
        for role in (None, *_ROLES):
            ids = {"draft": _draft_id(), "approved": _approved_id()}
            path = template.format(**ids)
            before = _app_state()
            calls_before = len(env["runner_calls"])
            res = _call(client, method, path, body, role)
            _assert_no_host_path(res.text, env, (method, path, role))
            if role is None:
                assert res.status_code == 401, (method, path, res.status_code, res.text)
            elif not _allowed(role, min_role):
                assert res.status_code == 403, (method, path, role, res.status_code, res.text)
            else:
                # No false positive: the handler ran and answered as it always did.
                assert res.status_code == ok_status, (method, path, role, res.status_code, res.text)
                continue
            assert _app_state() == before, (method, path, role)
            assert len(env["runner_calls"]) == calls_before, (method, path, role)
            assert not (Path(app_store.get_app(ids["draft"])["dir"]) / "Dockerfile").exists()


def test_connector_route_matrix_refusals_change_nothing_and_sufficient_roles_reach_handler(env):
    client = env["client"]
    chat_id = cursor_session.get_port().open_chat("dms", "seed")
    for method, template, body, min_role, ok_status in _CONNECTOR_ROUTES:
        path = template.format(chat=chat_id)
        for role in (None, *_ROLES):
            before = _connector_state()
            res = _call(client, method, path, body, role)
            _assert_no_host_path(res.text, env, (method, path, role))
            if role is None:
                assert res.status_code == 401, (method, path, res.status_code)
            elif not _allowed(role, min_role):
                assert res.status_code == 403, (method, path, role, res.status_code)
            else:
                assert res.status_code == ok_status, (method, path, role, res.status_code, res.text)
                continue
            assert _connector_state() == before, (method, path, role)


# --- no host path in any response body, even for an admin ---------------------


def test_app_records_hide_install_and_skin_dirs_but_keep_everything_else(env):
    client = env["client"]
    app_id = _draft_id()

    listed = client.get("/api/apps", headers=_hdr("viewer"))
    assert listed.status_code == 200
    _assert_no_host_path(listed.text, env, "list")
    apps = {a["id"]: a for a in listed.json()["apps"]}
    assert app_id in apps and app_store.BUILTIN_CONSTRUCTOR_ID in apps
    for app in apps.values():
        assert "dir" not in app
        assert "skin_dir" not in (app.get("manifest") or {})
    # What the UI actually uses is still there.
    assert apps[app_id]["status"] == "draft"
    assert apps[app_id]["name"] == "t2r-draft"
    assert apps[app_id]["about"]["summary"]
    builtin = apps[app_store.BUILTIN_CONSTRUCTOR_ID]
    assert builtin["manifest"]["launch_path"] == "/cortex/constructor/"

    one = client.get(f"/api/apps/{app_id}", headers=_hdr("admin"))
    assert one.status_code == 200
    _assert_no_host_path(one.text, env, "get")
    assert one.json()["app"]["id"] == app_id

    # The stored record still knows where the app lives; only the wire hides it.
    assert Path(app_store.get_app(app_id)["dir"]).is_dir()


def test_dockerize_reports_a_relative_path_and_writes_the_file(env):
    client = env["client"]
    app_id = _draft_id()
    res = client.post(f"/api/apps/{app_id}/dockerize", headers=_hdr("steward"))
    assert res.status_code == 200
    _assert_no_host_path(res.text, env, "dockerize")
    body = res.json()
    assert body["created"] is True
    assert body["path"] == "Dockerfile"
    assert (Path(app_store.get_app(app_id)["dir"]) / "Dockerfile").is_file()


def test_start_error_text_does_not_leak_a_host_path(env):
    client = env["client"]
    app_id = _approved_id()
    res = client.post(f"/api/apps/{app_id}/start", headers=_hdr("admin"))
    assert res.status_code == 409
    _assert_no_host_path(res.text, env, "start")
    detail = res.json()["detail"]
    assert detail["code"].startswith("start_spawn:")
    assert "No such file or directory" in detail["code"]
    assert len(env["runner_calls"]) == 1
    # The error is stored for the operator, and the next read is clean too.
    assert _SPAWN_ERROR_PATH in app_store.get_app(app_id)["last_error"]
    again = client.get(f"/api/apps/{app_id}", headers=_hdr("viewer"))
    _assert_no_host_path(again.text, env, "get-after-start")
    assert again.json()["app"]["explained_error"]["title"]


def test_mutation_responses_hide_dirs(env):
    client = env["client"]
    draft = _draft_id()
    for method, path, role in (
        ("POST", f"/api/apps/{draft}/rescan", "steward"),
        ("POST", f"/api/apps/{draft}/approve", "admin"),
        ("POST", f"/api/apps/{draft}/stop", "steward"),
    ):
        res = client.request(method, path, headers=_hdr(role))
        assert res.status_code == 200, (path, res.text)
        _assert_no_host_path(res.text, env, path)
        assert "dir" not in res.json()["app"]
    rejected = _draft_id()
    rej = client.post(f"/api/apps/{rejected}/reject", json={"reason": "no"}, headers=_hdr("steward"))
    assert rej.status_code == 200 and rej.json()["app"]["status"] == "rejected"
    _assert_no_host_path(rej.text, env, "reject")
    folder = client.post("/api/apps/import-folder", json={"path": _folder(env["tmp"])}, headers=_hdr("admin"))
    assert folder.status_code == 200 and folder.json()["app"]["status"] == "draft"
    assert folder.json()["app"]["name"] == "proj"
    _assert_no_host_path(folder.text, env, "import-folder")


def test_workspace_catalog_and_dispatch_hide_roots_but_keep_presence(env):
    client = env["client"]
    res = client.get("/api/connectors/workspaces", headers=_hdr("viewer"))
    assert res.status_code == 200
    _assert_no_host_path(res.text, env, "workspaces")
    rows = {w["id"]: w for w in res.json()["workspaces"]}
    assert {"cortex", "netie", "dms", "chatbot", "pointer", "omi", "openvault"} <= set(rows)
    for row in rows.values():
        assert "root" not in row and "windows_default" not in row
        assert row["env"].startswith("CORTEX_WS_")
    assert rows["dms"]["present"] is True
    assert rows["cortex"]["present"] is True

    sent = client.post(
        "/api/connectors/dispatch", json={"text": "ship the dms slice", "kind": "task"}, headers=_hdr("steward")
    )
    assert sent.status_code == 200
    _assert_no_host_path(sent.text, env, "dispatch")
    body = sent.json()
    assert "workspace_root" not in body
    assert body["workspace"] == "dms" and body["workspace_present"] is True
    assert body["new_cursor_chat"] is True and body["cursor_chat_id"]

    posted = client.post(
        "/api/connectors/agents/constructor/messages",
        json={"text": "build the desk", "kind": "task"},
        headers=_hdr("steward"),
    )
    assert posted.status_code == 200
    _assert_no_host_path(posted.text, env, "agent-post")
    assert "workspace_root" not in posted.json()["dispatch"]
    assert "build the desk" in [m["text"] for m in posted.json()["messages"]]


# --- archive limits -------------------------------------------------------------


def _import(client: TestClient, data: bytes):
    return client.post("/api/apps/import", json={"zip_base64": _b64(data)}, headers=_hdr("steward"))


def _assert_refused_without_trace(env: dict[str, Any], res: Any, before: Any, code: str) -> None:
    assert res.status_code == 400, res.text
    assert res.json()["detail"]["code"] == code
    _assert_no_host_path(res.text, env, code)
    assert _app_state() == before


def test_oversize_archive_is_refused_before_anything_is_written(env, monkeypatch):
    client = env["client"]
    data = _app_zip()
    monkeypatch.setattr(app_store, "MAX_ARCHIVE_BYTES", len(data) - 1)
    before = _app_state()
    _assert_refused_without_trace(env, _import(client, data), before, "archive_too_large")
    # The route also refuses an oversize body before decoding it.
    huge = "A" * ((len(data) // 3 + 2) * 4)
    res = client.post("/api/apps/import", json={"zip_base64": huge}, headers=_hdr("steward"))
    _assert_refused_without_trace(env, res, before, "archive_too_large")


def test_archive_that_expands_past_the_limit_is_refused(env, monkeypatch):
    client = env["client"]
    data = _zip({"main.py": "print('hi')\n", "pad.txt": "0" * 200_000})
    assert len(data) < 50_000  # small on the wire, large on disk
    monkeypatch.setattr(app_store, "MAX_ARCHIVE_UNCOMPRESSED_BYTES", 100_000)
    before = _app_state()
    _assert_refused_without_trace(env, _import(client, data), before, "archive_too_large")


def test_archive_with_too_many_entries_is_refused(env, monkeypatch):
    client = env["client"]
    monkeypatch.setattr(app_store, "MAX_ARCHIVE_ENTRIES", 5)
    data = _zip({"main.py": "print('hi')\n", **{f"f{i}.txt": "x" for i in range(5)}})
    before = _app_state()
    _assert_refused_without_trace(env, _import(client, data), before, "archive_too_many_entries")


@pytest.mark.parametrize(
    "member",
    [
        "../outside.txt",
        "a/../../outside.txt",
        "/abs/outside.txt",
        "..\\outside.txt",
        "C:\\outside.txt",
        "C:/outside.txt",
        "\\\\server\\share\\outside.txt",
    ],
)
def test_archive_with_an_escaping_member_is_refused_whole(env, member):
    client = env["client"]
    data = _zip({"main.py": "print('hi')\n", member: "escaped"})
    before = _app_state()
    _assert_refused_without_trace(env, _import(client, data), before, "archive_unsafe_path")
    assert not list(env["tmp"].rglob("outside.txt"))


def test_archive_member_cannot_reach_a_sibling_app_dir(env):
    """A name that climbs out and back into a sibling whose name shares the prefix."""
    client = env["client"]
    data = _zip({"main.py": "print('hi')\n", "../app-sibling/evil.txt": "x"})
    before = _app_state()
    _assert_refused_without_trace(env, _import(client, data), before, "archive_unsafe_path")
    assert not list((env["tmp"] / "apps").rglob("evil.txt"))


def test_normal_archive_at_the_limits_still_imports(env, monkeypatch):
    client = env["client"]
    data = _zip(
        {
            "main.py": "print('hi')\n",
            "requirements.txt": "fastapi\n",
            "pkg/__init__.py": "",
            "pkg/..hidden.py": "x = 1\n",  # dots inside a name are not a traversal
            "static/index.html": "<h1>hi</h1>",
        }
    )
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        entries = len(zf.infolist())
        expanded = sum(m.file_size for m in zf.infolist())
    monkeypatch.setattr(app_store, "MAX_ARCHIVE_BYTES", len(data))
    monkeypatch.setattr(app_store, "MAX_ARCHIVE_ENTRIES", entries)
    monkeypatch.setattr(app_store, "MAX_ARCHIVE_UNCOMPRESSED_BYTES", expanded)

    res = _import(client, data)
    assert res.status_code == 200, res.text
    _assert_no_host_path(res.text, env, "normal-import")
    app = res.json()["app"]
    assert app["status"] == "draft"
    assert app["unsafe_members"] == []
    stored = Path(app_store.get_app(app["id"])["dir"])
    assert (stored / "pkg" / "..hidden.py").read_text(encoding="utf-8") == "x = 1\n"
    assert (stored / "static" / "index.html").is_file()


def test_not_a_zip_is_refused_without_trace(env):
    client = env["client"]
    before = _app_state()
    res = _import(client, b"this is not a zip at all")
    assert res.status_code == 400
    assert res.json()["detail"]["code"].startswith("invalid_zip")
    _assert_no_host_path(res.text, env, "bad-zip")
    assert _app_state() == before
