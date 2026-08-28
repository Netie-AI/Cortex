"""Portable app/agent package + ship gate — "bring any AI-generated app".

Any stack, a zip from Cursor/Claude/anywhere: detection is generative-friendly
(auto-detect docker/node/python/static) but its OUTPUT is a pinned,
deterministic run manifest — re-importing the same tree always behaves
identically. Imported apps wire to the local AirGPT API (port 8765) via
``api_base`` and serve on their own assigned port from the 88xx range.

Ship gate (locked 2026-07-24): pack → secrets/key-leak scan → stress check →
**draft** awaiting one human approval, or **blocked**. Laptop→laptop sharing
is the zip itself; public domains ride the OpenVault + FreeBuild lane — this
module never tries to fake a public host.
"""

from __future__ import annotations

import hashlib
import json
import re
import socket
import zipfile
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

MANIFEST_NAME = "netie_app.json"
MANIFEST_SCHEMA = "netie.app/1"
DEFAULT_API_BASE = "http://127.0.0.1:8765"
RESERVED_PORTS = {8765}
PORT_RANGE = (8800, 8899)

EXCLUDE_DIRS = {".git", "node_modules", ".venv", "venv", "__pycache__", ".next"}
MAX_SCAN_BYTES = 1_000_000

_SECRET_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("openai_key", re.compile(r"sk-[A-Za-z0-9_\-]{20,}")),
    ("github_pat", re.compile(r"gh[ps]_[A-Za-z0-9]{36}")),
    ("aws_access_key", re.compile(r"AKIA[0-9A-Z]{16}")),
    ("google_api_key", re.compile(r"AIza[0-9A-Za-z_\-]{35}")),
    ("slack_token", re.compile(r"xox[bap]-[A-Za-z0-9\-]{10,}")),
    ("private_key_block", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    (
        "assigned_secret",
        re.compile(
            r"(?i)\b(api[_-]?key|secret|token|password)\b\s*[=:]\s*['\"][A-Za-z0-9_\-]{16,}['\"]"
        ),
    ),
]


def iter_files(root: str | Path) -> Iterator[tuple[str, Path]]:
    """Yield (posix relpath, absolute path), skipping vendored/derived trees."""
    base = Path(root)
    for path in sorted(base.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(base)
        if any(part in EXCLUDE_DIRS for part in rel.parts):
            continue
        if rel.name == MANIFEST_NAME:
            continue
        yield rel.as_posix(), path


# --- stack detection → deterministic manifest --------------------------------


def detect_stack(root: str | Path) -> str:
    base = Path(root)
    if (base / "Dockerfile").is_file() or (base / "dockerfile").is_file():
        return "docker"
    if (base / "package.json").is_file():
        return "node"
    if any((base / f).is_file() for f in ("pyproject.toml", "requirements.txt", "setup.py")):
        return "python"
    if any(base.glob("*.py")):
        return "python"
    if (base / "index.html").is_file():
        return "static"
    return "unknown"


def _python_entry(base: Path) -> str | None:
    for candidate in ("main.py", "app.py", "server.py", "run.py"):
        if (base / candidate).is_file():
            return candidate
    scripts = sorted(p.name for p in base.glob("*.py"))
    return scripts[0] if scripts else None


def _node_commands(base: Path, name: str) -> tuple[list[str], list[str], str | None]:
    build = ["npm", "ci"] if (base / "package-lock.json").is_file() else ["npm", "install"]
    entry: str | None = None
    start = ["npm", "start"]
    try:
        pkg = json.loads((base / "package.json").read_text(encoding="utf-8"))
        entry = pkg.get("main")
        if not (pkg.get("scripts") or {}).get("start"):
            start = ["node", entry or "index.js"]
    except Exception:
        pass
    return build, start, entry


def _commands(stack: str, base: Path, name: str) -> tuple[dict[str, list[str]], str | None]:
    if stack == "docker":
        return (
            {
                "build": ["docker", "build", "-t", name, "."],
                "start": ["docker", "run", "--rm", "-p", "{port}:{port}", name],
            },
            "Dockerfile",
        )
    if stack == "node":
        build, start, entry = _node_commands(base, name)
        return {"build": build, "start": start}, entry
    if stack == "python":
        entry = _python_entry(base)
        build = (
            ["pip", "install", "-r", "requirements.txt"]
            if (base / "requirements.txt").is_file()
            else []
        )
        start = ["python", entry] if entry else []
        return {"build": build, "start": start}, entry
    if stack == "static":
        return {"build": [], "start": ["python", "-m", "http.server", "{port}"]}, "index.html"
    return {"build": [], "start": []}, None


def content_sha256(root: str | Path) -> str:
    digest = hashlib.sha256()
    for rel, path in iter_files(root):
        digest.update(rel.encode("utf-8"))
        digest.update(hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()


def generate_manifest(
    root: str | Path, *, name: str | None = None, port: int | None = None
) -> dict[str, Any]:
    """Generative detection, deterministic output — no timestamps, no randomness."""
    base = Path(root)
    stack = detect_stack(base)
    app_name = re.sub(r"[^a-z0-9\-]+", "-", (name or base.name).lower()).strip("-") or "app"
    commands, entry = _commands(stack, base, app_name)
    return {
        "schema": MANIFEST_SCHEMA,
        "name": app_name,
        "version": "0.1.0",
        "stack": stack,
        "entry": entry,
        "commands": commands,
        "port": port,
        "api_base": DEFAULT_API_BASE,
        "needed_keys": [],
        "files": sum(1 for _ in iter_files(base)),
        "content_sha256": content_sha256(base),
    }


# --- secrets scan -------------------------------------------------------------


def scan_secrets(root: str | Path) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for rel, path in iter_files(root):
        try:
            if path.stat().st_size > MAX_SCAN_BYTES:
                continue
            raw = path.read_bytes()
            if b"\0" in raw[:1024]:
                continue
            text = raw.decode("utf-8", errors="ignore")
        except OSError:
            continue
        for line_no, line in enumerate(text.splitlines(), start=1):
            for pattern_name, pattern in _SECRET_PATTERNS:
                if pattern.search(line):
                    findings.append({"file": rel, "line": line_no, "pattern": pattern_name})
    return findings


# --- pack / unpack ------------------------------------------------------------


def pack(root: str | Path, out_zip: str | Path | None = None) -> dict[str, Any]:
    base = Path(root)
    manifest = generate_manifest(base)
    zip_path = Path(out_zip) if out_zip else base.parent / f"{manifest['name']}.netie.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for rel, path in iter_files(base):
            zf.write(path, rel)
        zf.writestr(MANIFEST_NAME, json.dumps(manifest, indent=2))
    return {
        "zip": str(zip_path),
        "sha256": hashlib.sha256(zip_path.read_bytes()).hexdigest(),
        "manifest": manifest,
    }


def safe_extract(zip_path: str | Path, dest: str | Path) -> list[str]:
    """Extract without letting members escape dest; returns the skipped names."""
    dest_dir = Path(dest).resolve()
    dest_dir.mkdir(parents=True, exist_ok=True)
    unsafe: list[str] = []
    with zipfile.ZipFile(zip_path) as zf:
        for member in zf.infolist():
            target = (dest_dir / member.filename).resolve()
            if not str(target).startswith(str(dest_dir)):
                unsafe.append(member.filename)
                continue
            zf.extract(member, dest_dir)
    return unsafe


def unpack(zip_path: str | Path, dest: str | Path) -> dict[str, Any]:
    """Zip-slip-safe extraction + content re-verification against the manifest."""
    dest_dir = Path(dest).resolve()
    unsafe = safe_extract(zip_path, dest_dir)
    manifest_file = dest_dir / MANIFEST_NAME
    if not manifest_file.is_file():
        return {"ok": False, "error": "manifest_missing", "unsafe_members": unsafe}
    try:
        manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
    except Exception as exc:
        return {"ok": False, "error": f"manifest_invalid:{exc}", "unsafe_members": unsafe}
    verified = manifest.get("content_sha256") == content_sha256(dest_dir)
    return {"ok": True, "manifest": manifest, "verified": verified, "unsafe_members": unsafe}


# --- ship gate ----------------------------------------------------------------


def ship_gate(
    root: str | Path,
    *,
    stress: Callable[[Path, dict[str, Any]], bool] | None = None,
) -> dict[str, Any]:
    """pack → secrets scan → stress → draft|blocked. Draft still needs a human."""
    base = Path(root)
    manifest = generate_manifest(base)
    reasons: list[str] = []

    if manifest["stack"] == "unknown":
        reasons.append("stack_unknown")

    findings = scan_secrets(base)
    if findings:
        reasons.append("secrets_found")

    if stress is not None:
        stress_ok = bool(stress(base, manifest))
    else:
        stress_ok = bool(manifest["commands"]["start"])
    if not stress_ok:
        reasons.append("stress_failed")

    status = "blocked" if reasons else "draft"
    return {
        "status": status,
        "stack": manifest["stack"],
        "manifest": manifest,
        "secret_findings": findings,
        "stress_ok": stress_ok,
        "reasons": reasons,
        "next": "human_approval" if status == "draft" else "fix_and_retry",
    }


# --- plain-English summary + auto-dockerize ----------------------------------

_STACK_WORDS = {
    "python": "a Python app",
    "node": "a Node.js app",
    "static": "a plain website (HTML files)",
    "docker": "a Docker app",
    "unknown": "an app we couldn't identify",
}


def describe(manifest: dict[str, Any], secret_findings: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """What is this app, and what will it be allowed to do? — approval-screen copy.

    People approve what they understand. This says it in one line plus bullets,
    with no stack traces, no manifest JSON, and no engine vocabulary.
    """
    stack = str(manifest.get("stack") or "unknown")
    files = int(manifest.get("files") or 0)
    summary = (
        f"This is {_STACK_WORDS.get(stack, _STACK_WORDS['unknown'])}"
        f" with {files} file{'s' if files != 1 else ''}."
    )

    will_do = ["Run on this computer, on its own port."]
    if stack != "static":
        will_do.append("Be able to talk to your local AI engine.")
    if manifest.get("commands", {}).get("build"):
        will_do.append("Download the pieces it needs the first time it starts.")

    watch_out: list[str] = []
    if secret_findings:
        files_hit = sorted({str(f.get("file")) for f in secret_findings})[:3]
        watch_out.append(
            "It has passwords or keys saved inside its files ("
            + ", ".join(files_hit)
            + "). Remove them before using this app."
        )
    if stack == "unknown":
        watch_out.append("We couldn't tell how to start it, so it can't run yet.")
    if stack == "docker":
        watch_out.append("Docker apps can't be started for you yet — you'd run this one yourself.")

    return {
        "summary": summary,
        "will_do": will_do,
        "watch_out": watch_out,
        "safe_to_approve": not watch_out,
    }


DOCKERFILE_NAME = "Dockerfile"

_DOCKERFILES = {
    "python": (
        "FROM python:3.12-slim\n"
        "WORKDIR /app\n"
        "COPY . .\n"
        "{install}"
        "ENV PORT=8080\n"
        "EXPOSE 8080\n"
        'CMD ["sh", "-c", "python {entry}"]\n'
    ),
    "node": (
        "FROM node:20-slim\n"
        "WORKDIR /app\n"
        "COPY . .\n"
        "{install}"
        "ENV PORT=8080\n"
        "EXPOSE 8080\n"
        'CMD ["sh", "-c", "{start}"]\n'
    ),
    "static": (
        "FROM python:3.12-slim\n"
        "WORKDIR /app\n"
        "COPY . .\n"
        "ENV PORT=8080\n"
        "EXPOSE 8080\n"
        'CMD ["sh", "-c", "python -m http.server $PORT"]\n'
    ),
}


def ensure_dockerfile(root: str | Path, manifest: dict[str, Any] | None = None) -> dict[str, Any]:
    """Write a Dockerfile for an app that doesn't have one — the "we do it for
    them" half of shipping. Deterministic, and never overwrites the author's own.
    """
    base = Path(root)
    manifest = manifest or generate_manifest(base)
    stack = str(manifest.get("stack") or "unknown")
    target = base / DOCKERFILE_NAME

    if target.exists() or (base / "dockerfile").exists():
        return {"created": False, "reason": "already_has_dockerfile", "path": str(target)}
    if stack not in _DOCKERFILES:
        return {"created": False, "reason": f"cannot_dockerize:{stack}", "path": None}

    template = _DOCKERFILES[stack]
    if stack == "python":
        install = (
            "RUN pip install --no-cache-dir -r requirements.txt\n"
            if (base / "requirements.txt").is_file()
            else ""
        )
        entry = manifest.get("entry") or "main.py"
        content = template.format(install=install, entry=entry)
    elif stack == "node":
        install = (
            "RUN npm ci --omit=dev\n"
            if (base / "package-lock.json").is_file()
            else "RUN npm install --omit=dev\n"
        )
        start = " ".join(manifest.get("commands", {}).get("start") or ["npm", "start"])
        content = template.format(install=install, start=start)
    else:
        content = template

    target.write_text(content, encoding="utf-8")
    return {"created": True, "reason": f"generated_for:{stack}", "path": str(target)}


def assign_port(preferred: int | None = None) -> int:
    """First free non-reserved port; 8765 stays the AirGPT API, always."""
    candidates: list[int] = []
    if preferred and preferred not in RESERVED_PORTS:
        candidates.append(preferred)
    candidates.extend(
        p for p in range(PORT_RANGE[0], PORT_RANGE[1] + 1) if p not in RESERVED_PORTS
    )
    for port in candidates:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            try:
                sock.bind(("127.0.0.1", port))
            except OSError:
                continue
            return port
    raise RuntimeError("no free port available in the app range")
