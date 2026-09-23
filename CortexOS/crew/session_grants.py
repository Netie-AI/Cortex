"""EPIC-GRANT-01: the session grant catalog store. Store only; nothing reaches the laptop.

Crew records an Allow / Cancel decision for exactly three catalog kinds before
any reach happens:

1. ``folder``: an absolute folder path that is not a drive root (``C:\\``),
   not POSIX ``/``, not a UNC share root, not the user profile root, and has
   no ``..`` component.
2. ``window``: an already-open window as UACC ``list_windows`` names it,
   ``title`` plus a positive integer ``pid``. A pid does not outlive the
   process, so a window grant is session-only and ``persist`` is refused.
3. ``office_file``: a named Office file (``OFFICE_EXTENSIONS``) that resolves
   under a folder this session already granted ``allow`` (session-only or
   persisted). A file outside every granted folder is refused.

``persist`` unset means session-only: the grant lives in process memory keyed
by ``session_id`` and dies with the process. ``persist=true`` writes the grant
to ``data/crew/session_grants.json`` (same convention as ``approvals.json``).
A refusal raises ``GrantRefused`` and leaves both stores unchanged.

Jail expansion (reaching a granted folder) is EPIC-GRANT-02: it lives in
``CortexOS/crew/workspace.py`` and calls ``granted_folder_for`` here, so there
is one store and one path normaliser. Every row carries ``destination`` and the
only value this engine can write is ``local``. The all-apps backdoor, cloud
upload, auto-launch, unattended Act, second mouse, fill-form and Excel Copilot
paste stay out of scope.

No ``from __future__ import annotations`` (FastAPI route module rule); the
pydantic body model is hoisted to module level.
"""

import itertools
import json
import os
import re
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

KIND_FOLDER = "folder"
KIND_WINDOW = "window"
KIND_OFFICE_FILE = "office_file"
KINDS = frozenset({KIND_FOLDER, KIND_WINDOW, KIND_OFFICE_FILE})

DECISION_ALLOW = "allow"
DECISION_CANCEL = "cancel"
DECISIONS = frozenset({DECISION_ALLOW, DECISION_CANCEL})

# EPIC-GRANT-02: every grant row says where files go. The only destination
# this engine has is the device itself; no off-device path is built here, so
# a row can never carry anything else (a stale file cannot smuggle one in).
DESTINATION_LOCAL = "local"

# Named Office documents only. Not .exe/.lnk/.bat: the catalog is files to
# read, not programs to launch (auto-launch is out of scope).
OFFICE_EXTENSIONS = frozenset(
    {".docx", ".xlsx", ".pptx", ".doc", ".xls", ".ppt", ".csv"}
)

LAW = (
    "Catalog kinds only: folder (not a drive root, not the profile root), "
    "window (title + pid from UACC list_windows, session-only), office_file "
    "(under a folder granted allow in this session). persist unset is "
    "session-only. Refusals leave the store unchanged. Store only: nothing "
    "reaches the laptop until EPIC-GRANT-02."
)

_SESSION_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_DRIVE_RE = re.compile(r"^[A-Za-z]:")
_DEVICE_PREFIX_RE = re.compile(r"^(\\\\|//)[?.](\\|/)")
_MAX_PATH_CHARS = 1024
_MAX_TITLE_CHARS = 512


class GrantRefused(ValueError):
    """The request is not a catalog item. Carries the HTTP status to answer with."""

    def __init__(self, reason: str, status: int = 403) -> None:
        super().__init__(reason)
        self.reason = reason
        self.status = status


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _new_id() -> str:
    return uuid.uuid4().hex[:12]


def _is_windows_style(raw: str) -> bool:
    return bool(_DRIVE_RE.match(raw)) or raw.startswith("\\\\") or "\\" in raw


def _profile_roots_from_env() -> list[str]:
    out: list[str] = []
    for var in ("USERPROFILE", "HOME"):
        value = (os.environ.get(var) or "").strip()
        if value:
            out.append(value)
    return out


def _normalise_windows(raw: str) -> str:
    path = PureWindowsPath(raw)
    if not path.drive:
        raise GrantRefused("folder path must be absolute (drive or UNC)")
    if not path.root:
        raise GrantRefused("folder path must be absolute (drive-relative refused)")
    parts = path.parts[1:]
    if not parts:
        if path.drive.startswith("\\\\"):
            raise GrantRefused("a UNC share root is not a grantable folder")
        raise GrantRefused(f"a drive root ({path.drive}\\) is not a grantable folder")
    if any(part == ".." for part in parts):
        raise GrantRefused("'..' traversal is refused")
    return str(path).rstrip("\\")


def _normalise_posix(raw: str) -> str:
    path = PurePosixPath(raw)
    if not path.is_absolute():
        raise GrantRefused("folder path must be absolute")
    parts = path.parts[1:]
    if not parts:
        raise GrantRefused("the filesystem root (/) is not a grantable folder")
    if any(part == ".." for part in parts):
        raise GrantRefused("'..' traversal is refused")
    return str(path)


def _normalise_any(raw: Any) -> tuple[str, bool]:
    """Return (normalised path, windows_style). Refuse empty / oversized / unsafe."""
    text = str(raw or "").strip().strip('"').strip()
    if not text:
        raise GrantRefused("empty path", 400)
    if len(text) > _MAX_PATH_CHARS:
        raise GrantRefused("path too long", 400)
    if "\x00" in text:
        raise GrantRefused("path contains a NUL byte", 400)
    if _DEVICE_PREFIX_RE.match(text):
        raise GrantRefused("device and verbatim path prefixes are not folder paths")
    if _is_windows_style(text):
        return _normalise_windows(text), True
    return _normalise_posix(text), False


def _key(normalised: str, windows_style: bool) -> str:
    return normalised.lower() if windows_style else normalised


def _parents(normalised: str, windows_style: bool) -> list[str]:
    if windows_style:
        wpath = PureWindowsPath(normalised)
        return [str(p).rstrip("\\") for p in wpath.parents]
    ppath = PurePosixPath(normalised)
    return [str(p) for p in ppath.parents]


def normalise_folder(raw: Any, profile_roots: list[str] | None = None) -> str:
    """Catalog kind 1. Refuses drive roots, ``/``, UNC share roots, the profile
    root, relative paths, ``..`` and device prefixes. ``profile_roots`` overrides
    the USERPROFILE / HOME lookup (tests inject it)."""
    normalised, windows_style = _normalise_any(raw)
    roots = profile_roots if profile_roots is not None else _profile_roots_from_env()
    for root in roots:
        try:
            root_norm, root_windows = _normalise_any(root)
        except GrantRefused:
            continue
        if root_windows == windows_style and _key(root_norm, root_windows) == _key(
            normalised, windows_style
        ):
            raise GrantRefused("the user profile root is not a grantable folder")
    return normalised


def normalise_office_file(raw: Any, granted_folders: list[str]) -> str:
    """Catalog kind 3. Extension in ``OFFICE_EXTENSIONS`` and a parent in
    ``granted_folders`` (already-normalised folder paths granted allow)."""
    normalised, windows_style = _normalise_any(raw)
    leaf = PureWindowsPath(normalised).name if windows_style else PurePosixPath(normalised).name
    suffix = os.path.splitext(leaf)[1].lower()
    if suffix not in OFFICE_EXTENSIONS:
        allowed = ", ".join(sorted(OFFICE_EXTENSIONS))
        raise GrantRefused(f"not a named Office file (extension must be one of {allowed})")
    granted = {_key(folder, _is_windows_style(folder)) for folder in granted_folders}
    parents = {_key(parent, windows_style) for parent in _parents(normalised, windows_style)}
    if not granted & parents:
        raise GrantRefused(
            "office file is outside every folder this session granted allow; "
            "grant the folder first"
        )
    return normalised


def validate_window(title: Any, pid: Any) -> tuple[str, int]:
    """Catalog kind 2. Identity is what UACC ``list_windows`` returns: title + pid."""
    text = str(title or "").strip()
    if not text:
        raise GrantRefused("window title is required", 400)
    if len(text) > _MAX_TITLE_CHARS:
        raise GrantRefused("window title too long", 400)
    if isinstance(pid, bool) or not isinstance(pid, int):
        raise GrantRefused("window pid must be a positive integer", 400)
    if pid <= 0:
        raise GrantRefused("window pid must be a positive integer", 400)
    return text, pid


class SessionGrantBook:
    """Per-session catalog. Session-only grants stay in memory; ``persist=True``
    grants are written to ``path``. ``path=None`` keeps everything in memory."""

    def __init__(self, path: Path | None = None, profile_roots: list[str] | None = None) -> None:
        self.path = path
        self.profile_roots = profile_roots
        self._lock = threading.Lock()
        self._session: dict[str, list[dict[str, Any]]] = {}
        self._persisted: dict[str, list[dict[str, Any]]] = {}
        self._seq = itertools.count(1)
        self._load()

    # ---- read -------------------------------------------------------------

    def grants_for(self, session_id: str) -> list[dict[str, Any]]:
        sid = self._session_id(session_id)
        with self._lock:
            rows = list(self._persisted.get(sid, ())) + list(self._session.get(sid, ()))
        rows.sort(key=lambda r: int(r.get("seq") or 0))
        return [{k: v for k, v in r.items() if k != "seq"} for r in rows]

    def snapshot(self, session_id: str) -> dict[str, Any]:
        grants = self.grants_for(session_id)
        return {
            "ok": True,
            "session_id": session_id,
            "grants": grants,
            "count": len(grants),
            "reach": False,
            "law": LAW,
        }

    def _granted_folders(self, sid: str) -> list[str]:
        rows = list(self._persisted.get(sid, ())) + list(self._session.get(sid, ()))
        return [
            r["path"]
            for r in rows
            if r["kind"] == KIND_FOLDER and r["decision"] == DECISION_ALLOW
        ]

    def granted_folder_for(self, session_id: str, path: Any) -> str | None:
        """EPIC-GRANT-02 jail lookup. ``path`` must already be the resolved real
        path (symlinks followed by the caller that touches the filesystem).
        Returns the folder this session granted ``allow`` that equals the path or
        is one of its parents, else ``None``. Comparison is component-wise (the
        same ``_parents`` / ``_key`` the office_file rule uses), so ``D:\\work``
        never admits ``D:\\work-evil``. A cancelled folder is not in the set;
        another session's folder is not in the set. Raises ``GrantRefused`` for a
        path that is not even a catalog candidate (drive root, ``/``, the profile
        root, ``..``, device prefix) so the caller can say why."""
        sid = self._session_id(session_id)
        normalised = normalise_folder(path, self.profile_roots)
        windows_style = _is_windows_style(normalised)
        with self._lock:
            folders = self._granted_folders(sid)
        candidates = [normalised, *_parents(normalised, windows_style)]
        keys = {_key(c, windows_style): c for c in candidates}
        for folder in folders:
            hit = keys.get(_key(folder, _is_windows_style(folder)))
            if hit is not None and _is_windows_style(folder) == windows_style:
                return folder
        return None

    def is_path_granted(self, session_id: str, path: Any) -> bool:
        try:
            return self.granted_folder_for(session_id, path) is not None
        except GrantRefused:
            return False

    # ---- write ------------------------------------------------------------

    def record(
        self,
        session_id: str,
        *,
        kind: str,
        decision: str = DECISION_ALLOW,
        persist: bool = False,
        path: str | None = None,
        title: str | None = None,
        pid: int | None = None,
    ) -> dict[str, Any]:
        """Validate, then upsert by identity. Raises ``GrantRefused`` before any write."""
        sid = self._session_id(session_id)
        kind_text = str(kind or "").strip().lower()
        if kind_text not in KINDS:
            raise GrantRefused(
                f"unknown grant kind '{kind}'; catalog is {', '.join(sorted(KINDS))}", 400
            )
        decision_text = str(decision or DECISION_ALLOW).strip().lower()
        if decision_text not in DECISIONS:
            raise GrantRefused(f"decision must be one of {', '.join(sorted(DECISIONS))}", 400)
        persist_flag = bool(persist)
        with self._lock:
            row = self._build(sid, kind_text, decision_text, persist_flag, path, title, pid)
            bucket = self._persisted if persist_flag else self._session
            rows = bucket.setdefault(sid, [])
            rows[:] = [r for r in rows if r["identity"] != row["identity"]]
            other = self._session if persist_flag else self._persisted
            if sid in other:
                other[sid] = [r for r in other[sid] if r["identity"] != row["identity"]]
            rows.append(row)
            if persist_flag:
                self._persist()
        return {k: v for k, v in row.items() if k != "seq"}

    def _build(
        self,
        sid: str,
        kind: str,
        decision: str,
        persist: bool,
        path: str | None,
        title: str | None,
        pid: int | None,
    ) -> dict[str, Any]:
        row: dict[str, Any] = {
            "id": _new_id(),
            "session_id": sid,
            "kind": kind,
            "decision": decision,
            "persist": persist,
            "scope": "persisted" if persist else "session",
            "destination": DESTINATION_LOCAL,
            "created_at": _now(),
            "seq": next(self._seq),
        }
        if kind == KIND_FOLDER:
            folder = normalise_folder(path, self.profile_roots)
            row["path"] = folder
            row["identity"] = f"folder:{_key(folder, _is_windows_style(folder))}"
        elif kind == KIND_WINDOW:
            if persist:
                raise GrantRefused(
                    "a window grant is identified by pid, which does not outlive the "
                    "session; persist is refused for windows",
                    400,
                )
            text, number = validate_window(title, pid)
            row["title"] = text
            row["pid"] = number
            row["identity"] = f"window:{number}:{text}"
        else:
            office = normalise_office_file(path, self._granted_folders(sid))
            row["path"] = office
            row["identity"] = f"office_file:{_key(office, _is_windows_style(office))}"
        return row

    @staticmethod
    def _session_id(raw: Any) -> str:
        text = str(raw or "").strip()
        if not _SESSION_ID_RE.match(text):
            raise GrantRefused("session_id is required (1-128 chars: letters, digits, . _ : -)", 400)
        return text

    # ---- persistence (approvals.json convention) --------------------------

    def _load(self) -> None:
        path = self.path
        if path is None or not path.is_file():
            return
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if not isinstance(raw, dict):
            return
        sessions = raw.get("sessions")
        if not isinstance(sessions, dict):
            return
        for sid, rows in sessions.items():
            if not isinstance(rows, list):
                continue
            kept = [r for r in rows if _row_ok(r)]
            for r in kept:
                r["seq"] = next(self._seq)
                r["destination"] = DESTINATION_LOCAL
            if kept:
                self._persisted[str(sid)] = kept

    def _persist(self) -> None:
        path = self.path
        if path is None:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "sessions": {
                sid: [{k: v for k, v in r.items() if k != "seq"} for r in rows]
                for sid, rows in self._persisted.items()
                if rows
            }
        }
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass


def _row_ok(row: Any) -> bool:
    if not isinstance(row, dict):
        return False
    if row.get("kind") not in KINDS or row.get("decision") not in DECISIONS:
        return False
    if row.get("kind") == KIND_WINDOW:
        return False  # never persisted; a stale file cannot smuggle one in
    return bool(row.get("identity")) and bool(row.get("path")) and bool(row.get("id"))


# ---- HTTP ---------------------------------------------------------------------


class SessionGrantIn(BaseModel):
    session_id: str = ""
    kind: str = ""
    decision: str = DECISION_ALLOW
    persist: bool = False
    path: str | None = None
    title: str | None = None
    pid: int | None = None


def mount_session_grants(router: APIRouter, book: SessionGrantBook) -> None:
    """GET lists one session's grants. POST records one decision. No reach."""

    @router.get("/session-grants")
    async def session_grants_list(session_id: str = "") -> dict[str, Any]:
        """That session's grants only (session-only plus persisted). No cross-session view."""
        try:
            return book.snapshot(session_id)
        except GrantRefused as exc:
            raise HTTPException(exc.status, exc.reason) from exc

    @router.post("/session-grants")
    async def session_grants_record(body: SessionGrantIn) -> dict[str, Any]:
        """Record Allow / Cancel for a catalog item. Refusal is 4xx and leaves the store unchanged."""
        try:
            grant = book.record(
                body.session_id,
                kind=body.kind,
                decision=body.decision,
                persist=body.persist,
                path=body.path,
                title=body.title,
                pid=body.pid,
            )
        except GrantRefused as exc:
            raise HTTPException(exc.status, exc.reason) from exc
        out = book.snapshot(body.session_id)
        out["grant"] = grant
        return out
