"""App process runner — static zip import → approve → start → HTTP → stop."""

from __future__ import annotations

import io
import socket
import time
import urllib.request
import zipfile

import pytest

from CortexOS.execution import app_store


def _zip_bytes(files: dict[str, str]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        for name, content in files.items():
            zf.writestr(name, content)
    return buffer.getvalue()


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(app_store, "DB_PATH", tmp_path / "apps.db")
    monkeypatch.setattr(app_store, "APPS_ROOT", tmp_path / "apps")
    app_store.init()


def _port_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        return sock.connect_ex(("127.0.0.1", port)) != 0


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def test_static_app_start_http_stop():
    imported = app_store.import_zip_bytes(
        _zip_bytes({"index.html": "<!doctype html><h1>hello-runner</h1>"}),
        name="Static Runner",
    )
    assert imported["ok"] is True
    app_id = imported["app"]["id"]
    assert imported["app"]["status"] == "draft"

    approved = app_store.approve(app_id)["app"]
    assert approved["status"] == "approved"
    assert approved["run_status"] in (None, "stopped", "")
    port = int(approved["port"])

    started = app_store.start_app(app_id)
    assert started["ok"] is True, started
    assert started["app"]["run_status"] == "running"
    assert started["app"]["pid"]

    body = ""
    deadline = time.time() + 15
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=2) as resp:
                body = resp.read().decode("utf-8", errors="replace")
                if resp.status == 200:
                    break
        except Exception:
            time.sleep(0.2)
    assert "hello-runner" in body

    stopped = app_store.stop_app(app_id)
    assert stopped["ok"] is True
    assert stopped["app"]["run_status"] == "stopped"
    assert stopped["app"]["pid"] is None

    # Give the OS a beat to release the bind.
    deadline = time.time() + 5
    while time.time() < deadline and not _port_free(port):
        time.sleep(0.1)
    assert _port_free(port)


def test_render_argv_rewrites_bare_python_to_sys_executable():
    """Manifests keep the portable token ``python``; spawn must use this interpreter.

    Cloud/agent images often only ship ``python3``. A bare ``python`` argv then
    raises FileNotFoundError and surfaces as ``start_spawn`` — a silent CI red
    that has nothing to do with the app under test.
    """
    import sys

    from CortexOS.execution import app_runner

    rendered = app_runner._render_argv(["python", "-m", "http.server", "{port}"], 8899)
    assert rendered == [sys.executable, "-m", "http.server", "8899"]
    rendered3 = app_runner._render_argv(["python3", "main.py"], 8801)
    assert rendered3 == [sys.executable, "main.py"]
    # Non-python argv is left alone (only {port} expands).
    assert app_runner._render_argv(["node", "server.js", "{port}"], 8802) == [
        "node",
        "server.js",
        "8802",
    ]


def test_docker_stack_refuses_start(tmp_path):
    imported = app_store.import_zip_bytes(
        _zip_bytes({"Dockerfile": "FROM scratch\n", "index.html": "<h1>x</h1>"}),
        name="Dockerish",
    )
    app = imported["app"]
    # Unknown/docker with Dockerfile detects as docker; may be draft if start cmd exists.
    if app["status"] == "draft":
        approved = app_store.approve(app["id"])["app"]
        out = app_store.start_app(approved["id"])
        assert out["ok"] is False
        assert "unsupported_stack" in str(out.get("error"))


def test_approve_does_not_auto_start():
    app = app_store.import_zip_bytes(
        _zip_bytes({"index.html": "<h1>idle</h1>"}), name="Idle"
    )["app"]
    approved = app_store.approve(app["id"])["app"]
    assert approved["run_status"] in (None, "stopped", "")
    assert not approved.get("pid")


def test_stop_never_kills_a_recycled_pid():
    """After an engine restart the handle is gone and the stored pid may have
    been recycled onto an unrelated process — that process must survive."""
    import subprocess
    import sys

    from CortexOS.execution import app_runner

    victim = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        app_runner._PROCS.pop("ghost-app", None)  # engine restarted: no handle

        # Port not listening → no evidence this pid is still our app.
        result = app_runner.stop(app_id="ghost-app", pid=victim.pid, port=_free_port())

        assert result["stopped"] is False
        assert result["reason"] == "stale_pid"
        time.sleep(0.5)
        assert victim.poll() is None  # innocent bystander untouched
    finally:
        victim.kill()
        victim.wait(timeout=5)


def test_start_refuses_a_port_someone_else_holds():
    """A squatter on the port would answer the health probe for a crashed app."""
    import sys

    from CortexOS.execution import app_runner

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as squatter:
        squatter.bind(("127.0.0.1", 0))
        squatter.listen(1)
        port = squatter.getsockname()[1]

        out = app_runner.start(
            app_id="squatted",
            stack="python",
            cwd=".",
            port=port,
            commands={"start": [sys.executable, "-c", "raise SystemExit(1)"]},
        )

    assert out["ok"] is False
    assert str(out["error"]).startswith("port_conflict")


def test_start_fails_fast_when_the_process_dies():
    import sys

    from CortexOS.execution import app_runner

    port = _free_port()
    started_at = time.time()
    out = app_runner.start(
        app_id="crasher",
        stack="python",
        cwd=".",
        port=port,
        commands={"start": [sys.executable, "-c", "raise SystemExit(3)"]},
    )
    elapsed = time.time() - started_at

    assert out["ok"] is False
    assert "process_exited" in str(out["error"])
    assert elapsed < app_runner.HEALTH_TIMEOUT_SEC  # no burning the full timeout


def test_stop_all_reaps_supervised_children():
    from CortexOS.execution import app_runner

    app = app_store.import_zip_bytes(
        _zip_bytes({"index.html": "<h1>reap</h1>"}), name="Reapable"
    )["app"]
    app_store.approve(app["id"])
    assert app_store.start_app(app["id"])["ok"] is True

    assert app_runner.stop_all() >= 1
    assert app_runner._PROCS == {}
    app_store.stop_app(app["id"])


def test_activity_lists_running(monkeypatch, tmp_path):
    monkeypatch.setenv("PACK", "dms")
    monkeypatch.setenv("DMS_AUTH_DISABLED", "1")
    from CortexOS.execution import routine_scheduler, scoreboard, workflow_store
    from fastapi.testclient import TestClient
    from CortexOS.api.app import create_app

    monkeypatch.setattr(scoreboard, "DB_PATH", tmp_path / "scoreboard.db")
    monkeypatch.setattr(routine_scheduler, "DB_PATH", tmp_path / "routines.db")
    monkeypatch.setattr(workflow_store, "DB_PATH", tmp_path / "wf.db")

    app = app_store.import_zip_bytes(
        _zip_bytes({"index.html": "<h1>live</h1>"}), name="Live"
    )["app"]
    app_store.approve(app["id"])
    started = app_store.start_app(app["id"])
    assert started["ok"] is True

    try:
        client = TestClient(create_app())
        activity = client.get("/api/engine/activity").json()
        assert activity["ok"] is True
        running = activity["apps"]["running"]
        assert any(r["id"] == app["id"] for r in running)
    finally:
        app_store.stop_app(app["id"])
        app_store.delete_app(app["id"])
