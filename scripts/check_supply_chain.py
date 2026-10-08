#!/usr/bin/env python3
"""Fail the build when the image supply chain drifts (HX-02, R13.1-R13.3).

Checks, all offline:

1. ``uv.lock`` is current: the project's locked version equals
   ``[project].version`` and its recorded ``requires-dist`` is exactly the set of
   specifiers in ``pyproject.toml`` (base plus every extra).
2. Every ``requirements/image-<variant>.lock.txt`` pins each requirement with
   ``==`` and at least one ``--hash=sha256:``, and nothing else (no URLs, no
   editable installs, no index overrides).
3. Each image lock is not stale: every dependency the image's extras declare in
   ``pyproject.toml``, plus the build backend, is present and its locked version
   satisfies the ``pyproject.toml`` specifier.
4. No denylisted version appears in any lock (litellm 1.82.7 and 1.82.8, the
   March 2026 compromise).
5. Every shipped Dockerfile installs third-party code only with
   ``--require-hashes -r requirements/image-<variant>.lock.txt`` and installs the
   project only with ``--no-deps``.
6. No Dockerfile and no compose file (``*.yml`` / ``*.yaml`` with ``services``)
   sets ``DMS_AUTH_DISABLED``, via a Dockerfile ``ENV`` instruction, a compose
   ``environment`` entry, or a compose ``env_file`` whose file literally assigns
   it. The named failure is ``AUTH_DISABLED_IN_IMAGE``.

Regenerate with ``uv lock`` and ``python scripts/lock_images.py``; never edit a
lock by hand. This script reports. It never installs or regenerates anything.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import tomllib
import yaml
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name
from packaging.version import Version

ROOT = Path(__file__).resolve().parents[1]

#: (package, version) pairs that must never appear in a lock.
DENYLIST: frozenset[tuple[str, str]] = frozenset(
    {("litellm", "1.82.7"), ("litellm", "1.82.8")}
)

#: variant -> extras the image installs. Mirrors ``scripts/lock_images.py``.
VARIANTS: dict[str, tuple[str, ...]] = {
    "constructor": ("dms",),
    "core": (),
    "full": ("full",),
}

#: shipped Dockerfile -> the lock variant it must install.
DOCKERFILES: dict[str, str] = {
    "Dockerfile": "constructor",
    "Dockerfile.constructor": "constructor",
    "Dockerfile.core": "core",
    "Dockerfile.full": "full",
}

_REQ_LINE = re.compile(r"^(?P<name>[A-Za-z0-9][A-Za-z0-9._-]*)==(?P<version>[^\s;\\]+)(?P<rest>.*)$")
_HASH = re.compile(r"^--hash=sha256:[0-9a-f]{64}$")
_AUTH_DISABLED_ASSIGN = re.compile(r"^(?:export\s+)?DMS_AUTH_DISABLED\s*=")
AUTH_DISABLED_IN_IMAGE = "AUTH_DISABLED_IN_IMAGE"

_SKIP_DIRS = frozenset(
    {
        ".git",
        ".venv",
        "venv",
        "node_modules",
        "__pycache__",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".next",
        "data",
        "dist",
        "build",
        ".eggs",
    }
)


def _pyproject(root: Path) -> dict:
    return tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))


def _norm_marker(marker: str) -> str:
    return re.sub(r"\s+", "", marker.replace('"', "'"))


def _declared_requires(project: dict) -> set[tuple]:
    out: set[tuple] = set()

    def add(spec: str, extra: str | None) -> None:
        req = Requirement(spec)
        marker = f"extra == '{extra}'" if extra else ""
        out.add(
            (
                canonicalize_name(req.name),
                frozenset(req.extras),
                _norm_marker(marker),
                frozenset(str(s) for s in req.specifier),
            )
        )

    for spec in project.get("dependencies", []):
        add(spec, None)
    for extra, specs in project.get("optional-dependencies", {}).items():
        for spec in specs:
            add(spec, extra)
    return out


def check_uv_lock(root: Path) -> list[str]:
    problems: list[str] = []
    project = _pyproject(root)["project"]
    name = canonicalize_name(project["name"])
    lock = tomllib.loads((root / "uv.lock").read_text(encoding="utf-8"))
    pkgs = [p for p in lock.get("package", []) if canonicalize_name(p["name"]) == name]
    if not pkgs:
        return [f"uv.lock: no entry for project {name!r}; run `uv lock`"]
    pkg = pkgs[0]
    if pkg.get("version") != project["version"]:
        problems.append(
            f"uv.lock: {name} is {pkg.get('version')!r}, pyproject.toml says "
            f"{project['version']!r}; run `uv lock`"
        )
    locked: set[tuple] = set()
    for row in pkg.get("metadata", {}).get("requires-dist", []):
        locked.add(
            (
                canonicalize_name(row["name"]),
                frozenset(row.get("extras", [])),
                _norm_marker(row.get("marker", "")),
                frozenset(s.strip() for s in row.get("specifier", "").split(",") if s.strip()),
            )
        )
    declared = _declared_requires(project)
    for row in sorted(declared - locked, key=repr):
        problems.append(f"uv.lock: pyproject requires {_fmt(row)} but uv.lock does not; run `uv lock`")
    for row in sorted(locked - declared, key=repr):
        problems.append(f"uv.lock: records {_fmt(row)} which pyproject.toml does not; run `uv lock`")
    for p in lock.get("package", []):
        key = (canonicalize_name(p["name"]), str(p.get("version", "")))
        if key in DENYLIST:
            problems.append(f"uv.lock: denylisted {key[0]}=={key[1]}")
    return problems


def _fmt(row: tuple) -> str:
    name, extras, marker, spec = row
    ex = f"[{','.join(sorted(extras))}]" if extras else ""
    mk = f" ; {marker}" if marker else ""
    return f"{name}{ex}{','.join(sorted(spec))}{mk}"


def parse_lock(path: Path) -> tuple[dict[str, str], list[str]]:
    """Return ({name: version}, problems) for a pip ``--generate-hashes`` lock."""
    problems: list[str] = []
    pins: dict[str, str] = {}
    logical: list[str] = []
    buf = ""
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.split(" #", 1)[0].rstrip() if not raw.lstrip().startswith("#") else ""
        if not line.strip():
            if buf:
                logical.append(buf)
                buf = ""
            continue
        if line.endswith("\\"):
            buf += line[:-1] + " "
            continue
        buf += line
        logical.append(buf)
        buf = ""
    if buf:
        logical.append(buf)

    rel = path.name
    for entry in logical:
        tokens = entry.split()
        m = _REQ_LINE.match(tokens[0]) if tokens else None
        if not m:
            problems.append(f"{rel}: not an exact pin: {tokens[0] if tokens else entry!r}")
            continue
        name = canonicalize_name(m.group("name"))
        hashes = [t for t in tokens[1:] if t.startswith("--hash")]
        other = [t for t in tokens[1:] if not t.startswith("--hash")]
        if not hashes:
            problems.append(f"{rel}: {name}=={m.group('version')} has no --hash")
        bad = [h for h in hashes if not _HASH.match(h)]
        if bad:
            problems.append(f"{rel}: {name} has a malformed hash {bad[0]!r}")
        if other:
            problems.append(f"{rel}: {name} carries unexpected tokens {other}")
        if name in pins:
            problems.append(f"{rel}: {name} is pinned twice")
        pins[name] = m.group("version")
        if (name, m.group("version")) in DENYLIST:
            problems.append(f"{rel}: denylisted {name}=={m.group('version')}")
    return pins, problems


def check_image_lock(root: Path, variant: str) -> list[str]:
    path = root / "requirements" / f"image-{variant}.lock.txt"
    if not path.is_file():
        return [f"{path.relative_to(root)}: missing; run `python scripts/lock_images.py {variant}`"]
    pins, problems = parse_lock(path)
    doc = _pyproject(root)
    project = doc["project"]
    specs = list(project.get("dependencies", []))
    for extra in VARIANTS[variant]:
        specs += project.get("optional-dependencies", {}).get(extra, [])
    specs += doc.get("build-system", {}).get("requires", [])
    for spec in specs:
        req = Requirement(spec)
        name = canonicalize_name(req.name)
        locked = pins.get(name)
        if locked is None:
            problems.append(
                f"{path.name}: stale, {spec!r} is not locked; "
                f"run `python scripts/lock_images.py {variant}`"
            )
        elif req.specifier and Version(locked) not in req.specifier:
            problems.append(
                f"{path.name}: stale, {name}=={locked} does not satisfy {spec!r}; "
                f"run `python scripts/lock_images.py {variant}`"
            )
    return problems


def _pip_installs(dockerfile_text: str) -> list[str]:
    text = re.sub(r"\\\n", " ", dockerfile_text)
    commands: list[str] = []
    for line in text.splitlines():
        if line.lstrip().startswith("#"):
            continue
        for part in re.split(r"&&|;|\|\|", line):
            if re.search(r"\bpip3?\s+install\b", part):
                commands.append(" ".join(part.split()))
    return commands


def check_dockerfile(root: Path, name: str, variant: str) -> list[str]:
    path = root / name
    if not path.is_file():
        return [f"{name}: missing"]
    installs = _pip_installs(path.read_text(encoding="utf-8"))
    lock = f"requirements/image-{variant}.lock.txt"
    problems: list[str] = []
    if not installs:
        problems.append(f"{name}: no pip install found")
    locked_install = False
    for cmd in installs:
        if "--no-deps" in cmd.split():
            continue
        if "--require-hashes" in cmd.split() and re.search(rf"-r\s+{re.escape(lock)}(\s|$)", cmd):
            locked_install = True
            continue
        problems.append(f"{name}: unhashed install {cmd!r}; use --require-hashes -r {lock} or --no-deps")
    if installs and not locked_install:
        problems.append(f"{name}: never installs {lock} with --require-hashes")
    return problems


def _rel(root: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def _is_dockerfile(name: str) -> bool:
    return name == "Dockerfile" or name.startswith("Dockerfile.")


def _logical_dockerfile_lines(text: str) -> list[tuple[int, str]]:
    logical: list[tuple[int, str]] = []
    buf = ""
    start = 1
    for i, raw in enumerate(text.splitlines(), 1):
        if not buf and (not raw.strip() or raw.lstrip().startswith("#")):
            continue
        if not buf:
            start = i
        piece = raw.strip()
        if piece.endswith("\\"):
            buf += piece[:-1].rstrip() + " "
            continue
        buf += piece
        logical.append((start, buf.strip()))
        buf = ""
    return logical


def _instruction(line: str) -> tuple[str, str]:
    if line.split(None, 1)[0].upper() == "ONBUILD":
        line = line.split(None, 1)[1] if " " in line else ""
    if not line:
        return "", ""
    parts = line.split(None, 1)
    return parts[0].upper(), (parts[1] if len(parts) > 1 else "")


def _env_instruction_sets_flag(rest: str) -> bool:
    tokens = rest.split()
    if not tokens:
        return False
    if "=" not in tokens[0]:
        return tokens[0] == "DMS_AUTH_DISABLED"
    return any(token.split("=", 1)[0] == "DMS_AUTH_DISABLED" for token in tokens)


def _environment_sets_flag(env: object) -> bool:
    if isinstance(env, dict):
        return "DMS_AUTH_DISABLED" in env
    if isinstance(env, str):
        env = [env]
    if isinstance(env, list):
        for item in env:
            if isinstance(item, str) and item.split("=", 1)[0].strip() == "DMS_AUTH_DISABLED":
                return True
    return False


def _env_file_entries(raw: object) -> list[str]:
    items = raw if isinstance(raw, list) else [raw]
    paths: list[str] = []
    for item in items:
        if isinstance(item, str) and item.strip():
            paths.append(item.strip())
        elif isinstance(item, dict):
            path = item.get("path") or item.get("file")
            if isinstance(path, str) and path.strip():
                paths.append(path.strip())
    return paths


def _text_assigns_flag(text: str) -> bool:
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if _AUTH_DISABLED_ASSIGN.match(stripped):
            return True
    return False


def _file_assigns_flag(path: Path) -> bool:
    if not path.is_file():
        return False
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return False
    return _text_assigns_flag(text)


def _has_services(node: object) -> bool:
    if isinstance(node, dict):
        if isinstance(node.get("services"), dict):
            return True
        return any(_has_services(value) for value in node.values())
    if isinstance(node, list):
        return any(_has_services(value) for value in node)
    return False


def _iter_services(node: object):
    if isinstance(node, dict):
        services = node.get("services")
        if isinstance(services, dict):
            for name, spec in services.items():
                if isinstance(spec, dict):
                    yield str(name), spec
        for value in node.values():
            yield from _iter_services(value)
    elif isinstance(node, list):
        for value in node:
            yield from _iter_services(value)


def _scan_dockerfile(root: Path, path: Path) -> list[str]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return []
    rel = _rel(root, path)
    problems: list[str] = []
    for lineno, line in _logical_dockerfile_lines(text):
        op, rest = _instruction(line)
        if op == "ENV" and _env_instruction_sets_flag(rest):
            problems.append(
                f"{AUTH_DISABLED_IN_IMAGE}: {rel} sets DMS_AUTH_DISABLED via ENV (line {lineno})"
            )
    return problems


def _scan_compose(root: Path, path: Path) -> list[str]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return []
    if "services:" not in text and "services :" not in text:
        return []
    try:
        docs = list(yaml.safe_load_all(text))
    except yaml.YAMLError:
        if _text_assigns_flag(text):
            return [
                f"{AUTH_DISABLED_IN_IMAGE}: {_rel(root, path)} sets DMS_AUTH_DISABLED "
                "(compose YAML did not parse)"
            ]
        return []
    if not any(_has_services(doc) for doc in docs):
        return []
    rel = _rel(root, path)
    problems: list[str] = []
    for doc in docs:
        if not isinstance(doc, dict):
            continue
        if _environment_sets_flag(doc.get("environment")):
            problems.append(f"{AUTH_DISABLED_IN_IMAGE}: {rel} sets DMS_AUTH_DISABLED via environment")
        for entry in _env_file_entries(doc.get("env_file")):
            env_path = Path(entry)
            if not env_path.is_absolute():
                env_path = path.parent / env_path
            if _file_assigns_flag(env_path):
                problems.append(
                    f"{AUTH_DISABLED_IN_IMAGE}: {rel} sets DMS_AUTH_DISABLED via env_file {entry}"
                )
        for name, spec in _iter_services(doc):
            if _environment_sets_flag(spec.get("environment")):
                problems.append(
                    f"{AUTH_DISABLED_IN_IMAGE}: {rel} service {name} sets "
                    "DMS_AUTH_DISABLED via environment"
                )
            for entry in _env_file_entries(spec.get("env_file")):
                env_path = Path(entry)
                if not env_path.is_absolute():
                    env_path = path.parent / env_path
                if _file_assigns_flag(env_path):
                    problems.append(
                        f"{AUTH_DISABLED_IN_IMAGE}: {rel} service {name} sets "
                        f"DMS_AUTH_DISABLED via env_file {entry}"
                    )
    return problems


def scan_dms_auth_disabled(root: Path) -> list[str]:
    """Named ``AUTH_DISABLED_IN_IMAGE`` findings for Dockerfiles and compose files."""
    problems: list[str] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS and not d.endswith(".egg-info")]
        for name in filenames:
            path = Path(dirpath) / name
            if _is_dockerfile(name):
                problems.extend(_scan_dockerfile(root, path))
            elif name.endswith((".yml", ".yaml")):
                problems.extend(_scan_compose(root, path))
    return problems


def run(root: Path = ROOT) -> list[str]:
    problems = scan_dms_auth_disabled(root)
    problems += check_uv_lock(root)
    for variant in VARIANTS:
        problems += check_image_lock(root, variant)
    for name, variant in DOCKERFILES.items():
        problems += check_dockerfile(root, name, variant)
    return problems


def main(root: Path | None = None) -> int:
    target = ROOT if root is None else root
    if (target / "pyproject.toml").is_file():
        problems = run(target)
    else:
        # Fixture trees and partial checkouts still fail closed on the auth flag.
        problems = scan_dms_auth_disabled(target)
    for p in problems:
        print(f"FAIL {p}")
    if problems:
        print(f"supply chain: {len(problems)} problem(s)")
        return 1
    if (target / "pyproject.toml").is_file():
        print(
            f"supply chain OK: uv.lock current, {len(VARIANTS)} hashed image locks, "
            f"{len(DOCKERFILES)} Dockerfiles, denylist clean, no DMS_AUTH_DISABLED in images"
        )
    else:
        print("supply chain OK: no DMS_AUTH_DISABLED in Dockerfile or compose files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
