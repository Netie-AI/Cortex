"""Process supervisor for approved apps — start/stop on assigned 88xx ports.

Supports python + static fully; node when npm is on PATH; docker returns
unsupported_stack (never silent). No auto-start on approve — callers must
POST /api/apps/{id}/start explicitly.
"""

from __future__ import annotations

import atexit
import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from CortexOS.execution.app_package import DEFAULT_API_BASE

# app_id -> live Popen (in-process; reaped on stop / stale pid check)
_PROCS: dict[str, subprocess.Popen[Any]] = {}

SUPPORTED_STACKS = frozenset({"python", "static", "node"})
HEALTH_TIMEOUT_SEC = 15.0
BUILD_TIMEOUT_SEC = 120.0

# Manifests keep the portable token "python"; spawn must use this interpreter.
# Cloud/agent images and some CI shells only expose python3 — bare "python"
# raises FileNotFoundError and surfaces as start_spawn.
_PYTHON_TOKENS = frozenset({"python", "python3"})


def _render_argv(argv: list[str], port: int) -> list[str]:
    rendered = [part.replace("{port}", str(port)) for part in argv]
    if rendered and rendered[0] in _PYTHON_TOKENS:
        rendered = [sys.executable, *rendered[1:]]
    return rendered


def _port_listening(port: int, host: str = "127.0.0.1") -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        try:
            return sock.connect_ex((host, port)) == 0
        except OSError:
            return False


def _wait_port(
    port: int, proc: subprocess.Popen[Any] | None = None, timeout: float = HEALTH_TIMEOUT_SEC
) -> str:
    """Wait for our process to bind. Returns 'listening' | 'exited' | 'timeout'.

    A bare "something is listening" check is not proof of health: if the app
    crashes while any other process holds that port, the port answers and the
    start looks successful. So the spawned process must still be alive when the
    port comes up, and a dead process fails immediately instead of burning the
    whole timeout.
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        if _port_listening(port):
            return "listening" if proc is None or _alive(proc) else "exited"
        if proc is not None and not _alive(proc):
            return "exited"
        time.sleep(0.15)
    return "timeout"


def _alive(proc: subprocess.Popen[Any] | None) -> bool:
    return proc is not None and proc.poll() is None


def reap_stale(app_id: str, pid: int | None) -> None:
    """Drop in-memory handle if the OS process is gone; optionally signal known pid."""
    proc = _PROCS.get(app_id)
    if proc is not None and not _alive(proc):
        _PROCS.pop(app_id, None)
        return
    if proc is None and pid:
        try:
            os.kill(pid, 0)
        except OSError:
            return  # already dead
        # Process exists but we lost the handle (engine restart) — leave it;
        # stop() can still kill by pid.


def start(
    *,
    app_id: str,
    stack: str,
    cwd: str | Path,
    port: int,
    commands: dict[str, Any],
    existing_pid: int | None = None,
) -> dict[str, Any]:
    """Build (optional) then start. Returns {ok, pid?, error?}."""
    if stack == "docker":
        return {"ok": False, "error": "unsupported_stack:docker"}
    if stack not in SUPPORTED_STACKS:
        return {"ok": False, "error": f"unsupported_stack:{stack}"}

    reap_stale(app_id, existing_pid)
    if _alive(_PROCS.get(app_id)):
        return {"ok": False, "error": "already_running"}
    if _port_listening(port):
        # Ours (we have a pid on file) or a foreign squatter — either way never
        # spawn into an occupied port: the child would die on bind and the
        # squatter would answer the health probe in its place.
        return {"ok": False, "error": "already_running" if existing_pid else f"port_conflict:{port}"}

    if stack == "node" and shutil.which("npm") is None and shutil.which("node") is None:
        return {"ok": False, "error": "node_runtime_missing"}

    root = Path(cwd)
    if not root.is_dir():
        return {"ok": False, "error": "missing_install_dir"}

    build_cmd = list(commands.get("build") or [])
    start_cmd = list(commands.get("start") or [])
    if not start_cmd:
        return {"ok": False, "error": "no_start_command"}

    env = os.environ.copy()
    env["PORT"] = str(port)
    env["API_BASE"] = DEFAULT_API_BASE
    env.setdefault("PYTHONUNBUFFERED", "1")

    if build_cmd:
        rendered_build = _render_argv(build_cmd, port)
        try:
            built = subprocess.run(
                rendered_build,
                cwd=str(root),
                env=env,
                capture_output=True,
                text=True,
                timeout=BUILD_TIMEOUT_SEC,
                shell=False,
            )
        except subprocess.TimeoutExpired:
            return {"ok": False, "error": "build_timeout"}
        except OSError as exc:
            return {"ok": False, "error": f"build_spawn:{exc}"}
        if built.returncode != 0:
            detail = (built.stderr or built.stdout or "")[:300]
            return {"ok": False, "error": f"build_failed:{detail}"}

    rendered_start = _render_argv(start_cmd, port)
    try:
        proc = subprocess.Popen(
            rendered_start,
            cwd=str(root),
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            shell=False,
        )
    except OSError as exc:
        return {"ok": False, "error": f"start_spawn:{exc}"}

    _PROCS[app_id] = proc
    health = _wait_port(port, proc)
    if health != "listening":
        stop(app_id=app_id, pid=proc.pid, port=port)
        err = ""
        try:
            if proc.stderr:
                err = (proc.stderr.read() or b"")[:300].decode("utf-8", errors="replace")
        except Exception:
            pass
        reason = "process_exited" if health == "exited" else "health_timeout"
        detail = err or ("process died before binding" if health == "exited" else "port not listening")
        return {"ok": False, "error": f"{reason}:{detail}"}

    return {"ok": True, "pid": proc.pid, "port": port}


def stop(*, app_id: str, pid: int | None = None, port: int | None = None) -> dict[str, Any]:
    """Terminate then kill. Idempotent if already stopped.

    Killing by a bare pid is only safe when we can still tie that pid to this
    app: after an engine restart ``_PROCS`` is empty and the pid on file may
    have been recycled by the OS onto an unrelated process. So a pid without a
    live handle is signalled only while the app's port is still listening —
    otherwise it is reported as stale and nothing is killed.
    """
    proc = _PROCS.pop(app_id, None)
    target_pid = proc.pid if proc is not None else pid
    if target_pid is None:
        return {"ok": True, "stopped": False, "reason": "no_pid"}

    if proc is not None and _alive(proc):
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                pass
        return {"ok": True, "stopped": True, "pid": target_pid}

    if proc is not None:  # our handle, already exited
        return {"ok": True, "stopped": True, "pid": target_pid}

    if port is None or not _port_listening(int(port)):
        return {"ok": True, "stopped": False, "reason": "stale_pid", "pid": target_pid}

    try:
        os.kill(int(target_pid), 15)
        for _ in range(10):
            time.sleep(0.1)
            if not _port_listening(int(port)):
                return {"ok": True, "stopped": True, "pid": target_pid}
        os.kill(int(target_pid), 9)
    except OSError:
        return {"ok": True, "stopped": False, "reason": "already_dead", "pid": target_pid}

    return {"ok": True, "stopped": True, "pid": target_pid}


def stop_all() -> int:
    """Stop every app this process supervises — keeps an engine restart from
    orphaning children whose pids then go stale in the store."""
    stopped = 0
    for app_id in list(_PROCS):
        if stop(app_id=app_id).get("stopped"):
            stopped += 1
    return stopped


atexit.register(stop_all)
