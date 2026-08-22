"""App store — the "Add app" backend: import any zip → ship gate → human approval.

Any AI-generated app from anywhere lands here as a zip (a netie package or a
raw project tree — no manifest required). Import extracts zip-slip-safe, runs
the ship gate (detect → pinned manifest → secrets scan → stress), and stores
the result as **draft** (awaiting one human approval) or **blocked**. Approval
assigns a port from the 88xx range (8765 stays the AirGPT API), pins the
manifest into the tree, and moves it to data/apps/installed — from there
AirGPT runs it as a built app/agent. Public hosting rides the
OpenVault + FreeBuild lane, never this module.
"""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from CortexOS.execution import app_package
from CortexOS.paths import data_path

DB_PATH = data_path("engine", "apps.db")
APPS_ROOT = data_path("apps")

STATUS_DRAFT = "draft"
STATUS_BLOCKED = "blocked"
STATUS_APPROVED = "approved"
STATUS_REJECTED = "rejected"

BUILTIN_CONSTRUCTOR_ID = "builtin-constructor"
_lock = threading.Lock()

_JSON_FIELDS = ("manifest", "findings", "reasons", "unsafe_members")


def _conn() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH), check_same_thread=False, timeout=10.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def _ensure_runtime_columns(conn: sqlite3.Connection) -> None:
    existing = {row[1] for row in conn.execute("PRAGMA table_info(apps)").fetchall()}
    alters = {
        "run_status": "TEXT DEFAULT 'stopped'",
        "pid": "INTEGER",
        "started_at": "REAL",
        "last_error": "TEXT DEFAULT ''",
    }
    for col, decl in alters.items():
        if col not in existing:
            conn.execute(f"ALTER TABLE apps ADD COLUMN {col} {decl}")


def init() -> None:
    with _lock, _conn() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS apps (
              id TEXT PRIMARY KEY,
              name TEXT NOT NULL,
              stack TEXT DEFAULT 'unknown',
              status TEXT DEFAULT 'draft',
              port INTEGER,
              dir TEXT NOT NULL,
              manifest TEXT DEFAULT '{}',
              findings TEXT DEFAULT '[]',
              reasons TEXT DEFAULT '[]',
              unsafe_members TEXT DEFAULT '[]',
              rejected_reason TEXT DEFAULT '',
              created_at REAL,
              updated_at REAL,
              approved_at REAL,
              run_status TEXT DEFAULT 'stopped',
              pid INTEGER,
              started_at REAL,
              last_error TEXT DEFAULT ''
            );
            """
        )
        _ensure_runtime_columns(conn)


def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    out = dict(row)
    for field in _JSON_FIELDS:
        try:
            out[field] = json.loads(out.get(field) or "null") or ([] if field != "manifest" else {})
        except Exception:
            out[field] = [] if field != "manifest" else {}
    return out


def get_app(app_id: str) -> dict[str, Any] | None:
    with _conn() as conn:
        row = conn.execute("SELECT * FROM apps WHERE id = ?", (app_id,)).fetchone()
    return _row_to_dict(row) if row else None


def ensure_builtin_constructor(*, skin_dir: str | Path | None = None) -> dict[str, Any]:
    """Register Constructor as a hosted Cortex app (no extra process, no extra port)."""
    init()
    skin = Path(
        skin_dir
        or os.environ.get("CONSTRUCTOR_SKIN_DIR")
        or r"D:\Constructor"
    )
    now = time.time()
    manifest = {
        "schema": app_package.MANIFEST_SCHEMA,
        "name": "Constructor",
        "stack": "static",
        "builtin": True,
        "served_by": "cortex",
        "launch_path": "/cortex/constructor/",
        "skin_dir": str(skin),
    }
    existing = get_app(BUILTIN_CONSTRUCTOR_ID)
    with _lock, _conn() as conn:
        if existing is None:
            conn.execute(
                """
                INSERT INTO apps (
                  id, name, stack, status, port, dir, manifest, findings, reasons,
                  unsafe_members, created_at, updated_at, approved_at, run_status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    BUILTIN_CONSTRUCTOR_ID,
                    "Constructor",
                    "static",
                    STATUS_APPROVED,
                    None,
                    str(skin),
                    json.dumps(manifest),
                    "[]",
                    "[]",
                    "[]",
                    now,
                    now,
                    now,
                    "hosted",
                ),
            )
        else:
            conn.execute(
                """
                UPDATE apps SET name=?, stack=?, status=?, dir=?, manifest=?,
                                updated_at=?, run_status='hosted', port=NULL
                WHERE id=?
                """,
                (
                    "Constructor",
                    "static",
                    STATUS_APPROVED,
                    str(skin),
                    json.dumps(manifest),
                    now,
                    BUILTIN_CONSTRUCTOR_ID,
                ),
            )
    return {"ok": True, "app": get_app(BUILTIN_CONSTRUCTOR_ID)}


def list_apps() -> list[dict[str, Any]]:
    ensure_builtin_constructor()
    with _conn() as conn:
        rows = conn.execute("SELECT * FROM apps ORDER BY created_at DESC").fetchall()
    apps = [_row_to_dict(r) for r in rows]
    user = [a for a in apps if not (a.get("manifest") or {}).get("builtin")]
    hosted = [a for a in apps if (a.get("manifest") or {}).get("builtin")]
    return user + hosted


def import_zip_bytes(data: bytes, *, name: str | None = None) -> dict[str, Any]:
    """Any zip in → gated record out. Never trusts the archive."""
    init()
    app_id = "app-" + uuid.uuid4().hex[:8]
    incoming = APPS_ROOT / "incoming" / app_id
    zip_path = APPS_ROOT / "incoming" / f"{app_id}.zip"
    incoming.mkdir(parents=True, exist_ok=True)
    zip_path.write_bytes(data)
    try:
        unsafe = app_package.safe_extract(zip_path, incoming)
    except Exception as exc:
        shutil.rmtree(incoming, ignore_errors=True)
        zip_path.unlink(missing_ok=True)
        return {"ok": False, "error": f"invalid_zip:{exc}"}
    zip_path.unlink(missing_ok=True)

    report = app_package.ship_gate(incoming)
    manifest = report["manifest"]
    now = time.time()
    with _lock, _conn() as conn:
        conn.execute(
            """
            INSERT INTO apps (
              id, name, stack, status, dir, manifest, findings, reasons,
              unsafe_members, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                app_id,
                name or manifest["name"],
                manifest["stack"],
                report["status"],
                str(incoming),
                json.dumps(manifest),
                json.dumps(report["secret_findings"]),
                json.dumps(report["reasons"]),
                json.dumps(unsafe),
                now,
                now,
            ),
        )
    return {"ok": True, "app": get_app(app_id)}


MAX_FOLDER_FILES = 2000
MAX_FOLDER_BYTES = 200 * 1024 * 1024


def import_folder(path: str | Path, *, name: str | None = None) -> dict[str, Any]:
    """Point at a folder — people have projects on disk, not base64 strings.

    Zips it here so the whole import path stays one code path (and one gate).
    """
    import io
    import zipfile

    root = Path(path).expanduser()
    if not root.is_dir():
        return {"ok": False, "error": "missing_folder"}

    files = list(app_package.iter_files(root))
    if not files:
        return {"ok": False, "error": "empty_folder"}
    if len(files) > MAX_FOLDER_FILES:
        return {"ok": False, "error": f"folder_too_many_files:{len(files)}"}
    total = sum(p.stat().st_size for _, p in files)
    if total > MAX_FOLDER_BYTES:
        return {"ok": False, "error": f"folder_too_large:{total}"}

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for rel, file_path in files:
            zf.write(file_path, rel)
    return import_zip_bytes(buffer.getvalue(), name=name or root.name)


def dockerize(app_id: str) -> dict[str, Any]:
    """Give an app a Dockerfile it didn't have — the step toward hosting it."""
    init()
    record = get_app(app_id)
    if record is None:
        return {"ok": False, "error": "unknown_app"}
    directory = record.get("dir")
    if not directory or not Path(directory).is_dir():
        return {"ok": False, "error": "missing_install_dir"}

    out = app_package.ensure_dockerfile(directory, record.get("manifest") or {})
    return {"ok": True, "app": get_app(app_id), **out}


def _ports_in_use() -> set[int]:
    with _conn() as conn:
        rows = conn.execute(
            "SELECT port FROM apps WHERE port IS NOT NULL AND status = ?",
            (STATUS_APPROVED,),
        ).fetchall()
    return {int(r[0]) for r in rows if r[0] is not None}


def _assign_unique_port(preferred: int | None = None) -> int:
    """Socket probe plus DB uniqueness so two approved apps never share a port."""
    import socket

    taken = _ports_in_use()
    reserved = app_package.RESERVED_PORTS
    lo, hi = app_package.PORT_RANGE
    candidates: list[int] = []
    if preferred and preferred not in reserved and preferred not in taken:
        candidates.append(int(preferred))
    candidates.extend(p for p in range(lo, hi + 1) if p not in reserved and p not in taken)
    for port in candidates:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            try:
                sock.bind(("127.0.0.1", port))
            except OSError:
                continue
            return port
    raise RuntimeError("no free port available in the app range")


def approve(app_id: str) -> dict[str, Any]:
    """The one human approval: draft → port assigned → installed. Does not start."""
    record = get_app(app_id)
    if record is None:
        return {"ok": False, "error": "unknown_app"}
    if record["status"] != STATUS_DRAFT:
        return {"ok": False, "error": f"not_draft:{record['status']}"}

    port = _assign_unique_port(record["manifest"].get("port"))
    installed = APPS_ROOT / "installed" / app_id
    installed.parent.mkdir(parents=True, exist_ok=True)
    if installed.exists():
        shutil.rmtree(installed)
    shutil.move(record["dir"], str(installed))

    manifest = dict(record["manifest"])
    manifest["port"] = port
    (installed / app_package.MANIFEST_NAME).write_text(
        json.dumps(manifest, indent=2), encoding="utf-8"
    )
    now = time.time()
    with _lock, _conn() as conn:
        conn.execute(
            """
            UPDATE apps SET status = ?, port = ?, dir = ?, manifest = ?,
                            updated_at = ?, approved_at = ?,
                            run_status = 'stopped', pid = NULL, last_error = ''
            WHERE id = ?
            """,
            (STATUS_APPROVED, port, str(installed), json.dumps(manifest), now, now, app_id),
        )
    return {"ok": True, "app": get_app(app_id)}


def reject(app_id: str, reason: str = "rejected") -> dict[str, Any]:
    record = get_app(app_id)
    if record is None:
        return {"ok": False, "error": "unknown_app"}
    if record["status"] == STATUS_APPROVED:
        return {"ok": False, "error": "already_approved"}
    with _lock, _conn() as conn:
        conn.execute(
            "UPDATE apps SET status = ?, rejected_reason = ?, updated_at = ? WHERE id = ?",
            (STATUS_REJECTED, reason, time.time(), app_id),
        )
    return {"ok": True, "app": get_app(app_id)}


def rescan(app_id: str) -> dict[str, Any]:
    """Fix-and-retry loop: re-run the ship gate over the current tree."""
    record = get_app(app_id)
    if record is None:
        return {"ok": False, "error": "unknown_app"}
    if record["status"] not in (STATUS_DRAFT, STATUS_BLOCKED):
        return {"ok": False, "error": f"not_rescannable:{record['status']}"}

    report = app_package.ship_gate(Path(record["dir"]))
    with _lock, _conn() as conn:
        conn.execute(
            """
            UPDATE apps SET status = ?, stack = ?, manifest = ?, findings = ?,
                            reasons = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                report["status"],
                report["stack"],
                json.dumps(report["manifest"]),
                json.dumps(report["secret_findings"]),
                json.dumps(report["reasons"]),
                time.time(),
                app_id,
            ),
        )
    return {"ok": True, "app": get_app(app_id)}


def start_app(app_id: str) -> dict[str, Any]:
    """Start an approved app on its assigned port (python/static; node best-effort)."""
    from CortexOS.execution import app_runner

    init()
    record = get_app(app_id)
    if record is None:
        return {"ok": False, "error": "unknown_app"}
    if record["status"] != STATUS_APPROVED:
        return {"ok": False, "error": f"not_approved:{record['status']}"}

    manifest = record.get("manifest") or {}
    if manifest.get("builtin") and manifest.get("served_by") == "cortex":
        now = time.time()
        with _lock, _conn() as conn:
            conn.execute(
                """
                UPDATE apps SET run_status = 'hosted', pid = NULL, last_error = '',
                                updated_at = ?
                WHERE id = ?
                """,
                (now, app_id),
            )
        return {
            "ok": True,
            "hosted": True,
            "url": manifest.get("launch_path") or "/cortex/constructor/",
            "app": get_app(app_id),
        }

    if not record.get("port"):
        return {"ok": False, "error": "no_port"}

    result = app_runner.start(
        app_id=app_id,
        stack=record["stack"],
        cwd=record["dir"],
        port=int(record["port"]),
        commands=(record.get("manifest") or {}).get("commands") or {},
        existing_pid=record.get("pid"),
    )
    now = time.time()
    if not result.get("ok"):
        with _lock, _conn() as conn:
            conn.execute(
                """
                UPDATE apps SET run_status = 'stopped', pid = NULL,
                                last_error = ?, updated_at = ?
                WHERE id = ?
                """,
                (str(result.get("error") or "start_failed"), now, app_id),
            )
        return {"ok": False, "error": result.get("error"), "app": get_app(app_id)}

    with _lock, _conn() as conn:
        conn.execute(
            """
            UPDATE apps SET run_status = 'running', pid = ?, started_at = ?,
                            last_error = '', updated_at = ?
            WHERE id = ?
            """,
            (int(result["pid"]), now, now, app_id),
        )
    return {"ok": True, "app": get_app(app_id)}


def stop_app(app_id: str) -> dict[str, Any]:
    from CortexOS.execution import app_runner

    init()
    record = get_app(app_id)
    if record is None:
        return {"ok": False, "error": "unknown_app"}

    outcome = app_runner.stop(
        app_id=app_id,
        pid=record.get("pid"),
        port=int(record["port"]) if record.get("port") else None,
    )
    with _lock, _conn() as conn:
        conn.execute(
            """
            UPDATE apps SET run_status = 'stopped', pid = NULL, updated_at = ?
            WHERE id = ?
            """,
            (time.time(), app_id),
        )
    # The row is cleared either way, but say which it was: a stale pid means the
    # process was already gone, not that we terminated it.
    return {
        "ok": True,
        "app": get_app(app_id),
        "terminated": bool(outcome.get("stopped")),
        "reason": outcome.get("reason"),
    }


def list_running() -> list[dict[str, Any]]:
    init()
    return [
        {
            "id": a["id"],
            "name": a["name"],
            "port": a.get("port"),
            "pid": a.get("pid"),
            "stack": a.get("stack"),
        }
        for a in list_apps()
        if a.get("run_status") == "running" and a.get("status") == STATUS_APPROVED
    ]


def delete_app(app_id: str) -> bool:
    record = get_app(app_id)
    if record is None:
        return False
    if (record.get("manifest") or {}).get("builtin"):
        return False
    try:
        stop_app(app_id)
    except Exception:
        pass
    shutil.rmtree(record["dir"], ignore_errors=True)
    with _lock, _conn() as conn:
        conn.execute("DELETE FROM apps WHERE id = ?", (app_id,))
    return True
