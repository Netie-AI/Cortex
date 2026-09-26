"""HTTP surface for the app store — AirGPT's "Add app" contract.

Import any zip (base64 body — no multipart dependency), get a gated draft or
blocked record back; approve/reject/rescan complete the human loop.
"""

from __future__ import annotations

import base64
import binascii
import re
import tempfile
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from fastapi import Depends, HTTPException
from pydantic import BaseModel, Field

from CortexOS.execution import app_package, app_store, humanize
from CortexOS.paths import constructor_skin_dir, data_path, repo_root
from CortexOS.security.auth_port import require_role

# T2-RESP (#263): responses never carry an absolute host path, even to an
# authorized caller. Keys that only ever hold a host path are dropped, and any
# known host root that surfaces inside a string (an error text, a log line) is
# replaced with this placeholder.
HOST_PATH_PLACEHOLDER = "<host-path>"
_APP_PATH_KEYS = frozenset({"dir", "skin_dir"})
# Error text can quote any path the OS or a build tool printed (not only the
# roots above), so error fields also get a generic absolute-path pass. It is
# kept off ordinary fields because those legitimately hold URL paths such as
# ``launch_path``.
_ERROR_KEYS = frozenset({"error", "last_error", "explained_error", "detail"})
_PATH_CHARS = r"[^\s'\"<>|*?,;()\[\]]"
_ABS_PATH = re.compile(
    r"(?:(?<![\w])[A-Za-z]:[\\/]"  # drive letter: C:\ or C:/
    r"|(?<![\w\\])\\\\(?=\w)"  # UNC: \\server
    r"|(?<![\w/.:-])/(?=[\w.~-]))"  # POSIX root, not the // of a URL scheme
    + _PATH_CHARS
    + "*"
)


def redact_absolute_paths(text: str) -> str:
    """Replace every absolute POSIX, drive-letter or UNC path in ``text``."""
    return _ABS_PATH.sub(HOST_PATH_PLACEHOLDER, text)


def _host_roots(extra: Iterable[str | Path | None] = ()) -> list[str]:
    """Absolute host roots to redact, longest first so nested roots win."""
    candidates: list[str | Path | None] = [
        app_store.APPS_ROOT,
        app_store.DB_PATH.parent,
        data_path(),
        repo_root(),
        constructor_skin_dir(),
        tempfile.gettempdir(),
    ]
    try:
        candidates.append(Path.home())
    except Exception:  # noqa: BLE001 - no resolvable home is simply one root fewer
        pass
    candidates.extend(extra)
    roots: set[str] = set()
    for raw in candidates:
        if raw is None or str(raw).strip() == "":
            continue
        path = Path(raw)
        for form in {str(path), path.as_posix()}:
            # A bare anchor ("/" or "C:\\") would redact every path-like text.
            if len(form.rstrip("/\\")) > 2 and Path(form).is_absolute():
                roots.add(form.rstrip("/\\"))
        try:
            resolved = path.resolve()
        except Exception:  # noqa: BLE001
            continue
        for form in {str(resolved), resolved.as_posix()}:
            if len(form.rstrip("/\\")) > 2:
                roots.add(form.rstrip("/\\"))
    return sorted(roots, key=len, reverse=True)


def scrub_host_paths(
    value: Any,
    *,
    drop_keys: Iterable[str] = (),
    extra_roots: Iterable[str | Path | None] = (),
    error_text: bool = False,
) -> Any:
    """Return ``value`` with host-path keys dropped and host roots redacted.

    ``error_text=True`` treats the whole value as error text (an HTTP error
    detail), so every absolute path in it is redacted, not only known roots.
    """
    roots = _host_roots(extra_roots)
    # A root only matches at a path boundary, so "/tmp" never eats "/tmpl".
    pattern = (
        re.compile("(?:" + "|".join(re.escape(r) for r in roots) + r")(?![\w.-])")
        if roots
        else None
    )
    dropped = frozenset(drop_keys)

    def _walk(item: Any, in_error: bool) -> Any:
        if isinstance(item, dict):
            return {
                k: _walk(v, in_error or k in _ERROR_KEYS)
                for k, v in item.items()
                if k not in dropped
            }
        if isinstance(item, (list, tuple)):
            return [_walk(v, in_error) for v in item]
        if isinstance(item, str):
            out = pattern.sub(HOST_PATH_PLACEHOLDER, item) if pattern else item
            return redact_absolute_paths(out) if in_error else out
        return item

    return _walk(value, error_text)


def _public(value: Any, *, error_text: bool = False) -> Any:
    """What an app route may return: no install dir, no skin dir, no host root."""
    return scrub_host_paths(value, drop_keys=_APP_PATH_KEYS, error_text=error_text)


def _dockerfile_rel(out: dict[str, Any]) -> dict[str, Any]:
    """``ensure_dockerfile`` reports an absolute path; the UI only needs the name."""
    record = out.get("app") or {}
    raw = out.get("path")
    if raw:
        directory = record.get("dir")
        try:
            rel = Path(raw).relative_to(directory) if directory else Path(Path(raw).name)
        except ValueError:
            rel = Path(Path(raw).name)
        out = dict(out)
        out["path"] = rel.as_posix()
    return out


class ImportBody(BaseModel):
    name: str | None = None
    zip_base64: str = Field(..., min_length=4)


class ImportFolderBody(BaseModel):
    path: str = Field(..., min_length=1)
    name: str | None = None


class RejectBody(BaseModel):
    reason: str = "rejected"


def _found(record: dict[str, Any] | None) -> dict[str, Any]:
    if record is None:
        raise HTTPException(status_code=404, detail=humanize.explain("unknown_app"))
    return record


def _humanize(record: dict[str, Any] | None) -> dict[str, Any] | None:
    """Attach plain-English versions of anything the user might have to act on."""
    if not record:
        return record
    out = dict(record)
    reasons = out.get("reasons") or []
    if reasons:
        out["explained_reasons"] = humanize.explain_all([str(r) for r in reasons])
    if out.get("last_error"):
        out["explained_error"] = humanize.explain(str(out["last_error"]))
    if out.get("manifest"):
        # Approval-screen copy: people approve what they understand.
        out["about"] = app_package.describe(out["manifest"], out.get("findings") or [])
    return out


def _fail(status: int, code: str) -> None:
    """Errors reach the UI already readable, never as an engine code alone."""
    raise HTTPException(status_code=status, detail=_public(humanize.explain(code), error_text=True))


def _conflict(out: dict[str, Any]) -> None:
    raise HTTPException(status_code=409, detail=_public(str(out.get("error")), error_text=True))


# TRUST-01 (#257): every route is gated through the engine's auth port. Reads
# need viewer, drafts and lifecycle housekeeping need steward, and anything that
# reads the host filesystem, lets code run, or destroys an app needs admin.
_VIEWER = [Depends(require_role("viewer"))]
_STEWARD = [Depends(require_role("steward"))]
_ADMIN = [Depends(require_role("admin"))]


def register_app_routes(app: Any) -> None:
    @app.post("/api/apps/import", dependencies=_STEWARD)
    async def import_app(body: ImportBody) -> dict[str, Any]:
        # Refuse an oversize body before decoding it (base64 is 4 bytes per 3).
        if len(body.zip_base64) > (app_store.MAX_ARCHIVE_BYTES // 3 + 1) * 4:
            _fail(400, "archive_too_large")
        try:
            data = base64.b64decode(body.zip_base64, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise HTTPException(status_code=400, detail=f"invalid base64: {exc}") from exc
        out = app_store.import_zip_bytes(data, name=body.name)
        if not out.get("ok"):
            _fail(400, str(out.get("error")))
        out["app"] = _humanize(out.get("app"))
        return dict(_public(out))

    @app.post("/api/apps/import-folder", dependencies=_ADMIN)
    async def import_app_folder(body: ImportFolderBody) -> dict[str, Any]:
        """Point at a project folder — no zipping, no base64, no encoding step."""
        out = app_store.import_folder(body.path, name=body.name)
        if not out.get("ok"):
            _fail(400, str(out.get("error")))
        out["app"] = _humanize(out.get("app"))
        return dict(_public(out))

    @app.get("/api/apps", dependencies=_VIEWER)
    async def list_apps() -> dict[str, Any]:
        app_store.init()
        return dict(_public({"ok": True, "apps": [_humanize(a) for a in app_store.list_apps()]}))

    @app.get("/api/apps/{app_id}", dependencies=_VIEWER)
    async def get_app(app_id: str) -> dict[str, Any]:
        app_store.init()
        return dict(_public({"ok": True, "app": _humanize(_found(app_store.get_app(app_id)))}))

    @app.post("/api/apps/{app_id}/approve", dependencies=_ADMIN)
    async def approve_app(app_id: str) -> dict[str, Any]:
        app_store.init()
        _found(app_store.get_app(app_id))
        out = app_store.approve(app_id)
        if not out.get("ok"):
            _conflict(out)
        return dict(_public(out))

    @app.post("/api/apps/{app_id}/reject", dependencies=_STEWARD)
    async def reject_app(app_id: str, body: RejectBody | None = None) -> dict[str, Any]:
        app_store.init()
        _found(app_store.get_app(app_id))
        out = app_store.reject(app_id, (body.reason if body else "rejected"))
        if not out.get("ok"):
            _conflict(out)
        return dict(_public(out))

    @app.post("/api/apps/{app_id}/rescan", dependencies=_STEWARD)
    async def rescan_app(app_id: str) -> dict[str, Any]:
        app_store.init()
        _found(app_store.get_app(app_id))
        out = app_store.rescan(app_id)
        if not out.get("ok"):
            _conflict(out)
        return dict(_public(out))

    @app.post("/api/apps/{app_id}/start", dependencies=_ADMIN)
    async def start_app(app_id: str) -> dict[str, Any]:
        app_store.init()
        _found(app_store.get_app(app_id))
        out = app_store.start_app(app_id)
        if not out.get("ok"):
            _fail(409, str(out.get("error")))
        out["app"] = _humanize(out.get("app"))
        return dict(_public(out))

    @app.post("/api/apps/{app_id}/stop", dependencies=_STEWARD)
    async def stop_app(app_id: str) -> dict[str, Any]:
        app_store.init()
        _found(app_store.get_app(app_id))
        out = app_store.stop_app(app_id)
        if not out.get("ok"):
            _conflict(out)
        return dict(_public(out))

    @app.post("/api/apps/{app_id}/dockerize", dependencies=_STEWARD)
    async def dockerize_app(app_id: str) -> dict[str, Any]:
        """Write a Dockerfile for an app that doesn't ship one — one click to
        make it hostable. Never overwrites an author's own Dockerfile."""
        app_store.init()
        _found(app_store.get_app(app_id))
        out = app_store.dockerize(app_id)
        if not out.get("ok"):
            _fail(409, str(out.get("error")))
        out = _dockerfile_rel(out)
        out["app"] = _humanize(out.get("app"))
        return dict(_public(out))

    @app.delete("/api/apps/{app_id}", dependencies=_ADMIN)
    async def delete_app(app_id: str) -> dict[str, Any]:
        app_store.init()
        _found(app_store.get_app(app_id))
        return {"ok": app_store.delete_app(app_id)}
