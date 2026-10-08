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
   sets ``DMS_AUTH_DISABLED`` or ``CORTEX_DEV_MODE``, via a Dockerfile ``ENV``
   instruction, a compose ``environment`` entry, or a compose ``env_file`` whose
   file literally assigns it. Named failures: ``AUTH_DISABLED_IN_IMAGE`` and
   ``DEV_MODE_IN_IMAGE``. When the scan root is its own git work tree, the file
   list is ``git ls-files``. Untracked and gitignored files are never opened, so
   CI and a machine that copied ``dev.env.example`` to ``dev.env`` match.
   A directory with no ``.git`` is walked. If ``.git`` exists and ``git ls-files``
   fails, the scan stops with ``DEV_MODE_IN_IMAGE: GIT_LS_FILES_FAILED`` and does
   not walk. Walking after that failure would read an untracked ``dev.env``.
   ``docker-compose.dev.yml`` is scanned like every other compose file. It does
   not assign ``CORTEX_DEV_MODE``. Its ``env_file`` target is gitignored, so it
   is not in that list and is never read. ``env_file`` is applied when the
   container runs and is not baked into the image, which is why a named
   exemption was considered and is not used.
   Shipped Dockerfiles (``Dockerfile``, ``Dockerfile.constructor``,
   ``Dockerfile.core``, ``Dockerfile.full``) must set ``DMS_REFUSE_DEMO_KEYS=1``.
   A tracked compose file or tracked ``env_file`` that sets that key to any
   other value, including empty, ``false``, ``0``, ``true``, or ``yes``, is
   ``DEMO_KEYS_IN_IMAGE`` and names the file. A compose file that does not
   mention the key is clean, because the image ``ENV`` still applies. The same
   ``git ls-files`` list and ``GIT_LS_FILES_FAILED`` stop apply.

Regenerate with ``uv lock`` and ``python scripts/lock_images.py``; never edit a
lock by hand. This script reports. It never installs or regenerates anything.
"""

from __future__ import annotations

import os
import re
import subprocess
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
_DEV_MODE_ASSIGN = re.compile(r"^(?:export\s+)?CORTEX_DEV_MODE\s*=")
AUTH_DISABLED_IN_IMAGE = "AUTH_DISABLED_IN_IMAGE"
DEV_MODE_IN_IMAGE = "DEV_MODE_IN_IMAGE"
DEMO_KEYS_IN_IMAGE = "DEMO_KEYS_IN_IMAGE"
GIT_LS_FILES_FAILED = "GIT_LS_FILES_FAILED"
_DEMO_KEY_NAME = "DMS_REFUSE_DEMO_KEYS"
_DEMO_KEY_ASSIGN = re.compile(rf"^(?:export\s+)?{_DEMO_KEY_NAME}\s*=\s*(.*)$")
# Exact repo-relative paths. Basename would also match night_shift/Dockerfile.
_SHIPPED_DOCKERFILES = frozenset(
    {
        "Dockerfile",
        "Dockerfile.constructor",
        "Dockerfile.core",
        "Dockerfile.full",
    }
)
# (env name, finding, assignment regex). Lock comparison above does not read this.
_IMAGE_ENV_FLAGS: tuple[tuple[str, str, re.Pattern[str]], ...] = (
    ("DMS_AUTH_DISABLED", AUTH_DISABLED_IN_IMAGE, _AUTH_DISABLED_ASSIGN),
    ("CORTEX_DEV_MODE", DEV_MODE_IN_IMAGE, _DEV_MODE_ASSIGN),
)

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


def _git_subprocess_env() -> dict[str, str]:
    env = os.environ.copy()
    for key in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE"):
        env.pop(key, None)
    return env


def _git_tracked(root: Path) -> tuple[set[str] | None, str | None]:
    """Repo-relative paths from ``git ls-files``.

    ``(None, None)`` only when ``root`` has no ``.git`` at all. The caller may
    walk that tree. Fixture directories are in this set.

    When ``.git`` exists, a failed ``git ls-files`` returns
    ``(None, GIT_LS_FILES_FAILED)``. The caller must stop with that name and
    must not walk. A walk would read an untracked ``dev.env``.
    """
    root = root.resolve()
    if not (root / ".git").exists():
        return None, None
    env = _git_subprocess_env()
    try:
        listed = subprocess.run(
            ["git", "-C", str(root), "ls-files", "-z"],
            capture_output=True,
            check=False,
            env=env,
        )
    except OSError:
        return None, GIT_LS_FILES_FAILED
    if listed.returncode != 0:
        return None, GIT_LS_FILES_FAILED
    try:
        top = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--show-toplevel"],
            capture_output=True,
            check=False,
            env=env,
        )
    except OSError:
        return None, GIT_LS_FILES_FAILED
    if top.returncode != 0:
        return None, GIT_LS_FILES_FAILED
    top_text = top.stdout.decode().strip()
    if not top_text or Path(top_text).resolve() != root:
        return None, GIT_LS_FILES_FAILED
    raw = listed.stdout.decode("utf-8", errors="surrogateescape")
    return {p for p in raw.split("\0") if p}, None


def _env_path_if_scannable(
    root: Path, compose: Path, entry: str, tracked: set[str] | None
) -> Path | None:
    """Env file to open, or None when it must not be read.

    A git work tree opens a path only when ``git ls-files`` lists it.
    """
    env_path = Path(entry)
    if not env_path.is_absolute():
        env_path = compose.parent / env_path
    try:
        resolved = env_path.resolve()
    except OSError:
        return None
    if tracked is None:
        return resolved
    try:
        rel = resolved.relative_to(root.resolve()).as_posix()
    except ValueError:
        return None
    if rel not in tracked:
        return None
    return resolved


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


def _env_instruction_sets_flag(rest: str, name: str) -> bool:
    tokens = rest.split()
    if not tokens:
        return False
    if "=" not in tokens[0]:
        return tokens[0] == name
    return any(token.split("=", 1)[0] == name for token in tokens)


def _env_flag_values(rest: str, name: str) -> list[str]:
    tokens = rest.split()
    if not tokens:
        return []
    if "=" not in tokens[0]:
        if tokens[0] == name:
            return [tokens[1] if len(tokens) > 1 else ""]
        return []
    found: list[str] = []
    for token in tokens:
        key, sep, val = token.partition("=")
        if sep and key == name:
            found.append(val)
    return found


def _demo_value_allowed(value: object) -> bool:
    """True only for the exact value ``1``. ``true`` and ``yes`` are not enough."""
    if isinstance(value, bool) or value is None:
        return False
    if isinstance(value, int):
        return value == 1
    if isinstance(value, str):
        raw = value.strip()
        if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in {'"', "'"}:
            raw = raw[1:-1]
        return raw == "1"
    return False


def _demo_value_shown(value: object) -> str:
    if isinstance(value, str):
        return value if value else "''"
    return repr(value)


def _env_text_bad_demo_values(text: str) -> list[str]:
    bad: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        match = _DEMO_KEY_ASSIGN.match(stripped)
        if not match:
            continue
        if not _demo_value_allowed(match.group(1)):
            bad.append(_demo_value_shown(match.group(1).strip()))
    return bad


def _file_bad_demo_values(path: Path) -> list[str]:
    if not path.is_file():
        return []
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return []
    return _env_text_bad_demo_values(text)


def _environment_demo_values(env: object) -> list[object]:
    if isinstance(env, dict):
        if _DEMO_KEY_NAME in env:
            return [env[_DEMO_KEY_NAME]]
        return []
    if isinstance(env, str):
        env = [env]
    found: list[object] = []
    if isinstance(env, list):
        for item in env:
            if isinstance(item, str):
                key, sep, val = item.partition("=")
                if sep and key.strip() == _DEMO_KEY_NAME:
                    found.append(val)
    return found


def _environment_sets_flag(env: object, name: str) -> bool:
    if isinstance(env, dict):
        return name in env
    if isinstance(env, str):
        env = [env]
    if isinstance(env, list):
        for item in env:
            if isinstance(item, str) and item.split("=", 1)[0].strip() == name:
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


def _text_assigns_flag(text: str, assign: re.Pattern[str]) -> bool:
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if assign.match(stripped):
            return True
    return False


def _file_assigns_flag(path: Path, assign: re.Pattern[str]) -> bool:
    if not path.is_file():
        return False
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return False
    return _text_assigns_flag(text, assign)


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


def _scan_dockerfile(root: Path, path: Path, *, require_demo_key: bool) -> list[str]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return []
    rel = _rel(root, path)
    problems: list[str] = []
    demo_values: list[str] = []
    for lineno, line in _logical_dockerfile_lines(text):
        op, rest = _instruction(line)
        if op != "ENV":
            continue
        for flag, finding, _assign in _IMAGE_ENV_FLAGS:
            if _env_instruction_sets_flag(rest, flag):
                problems.append(
                    f"{finding}: {rel} sets {flag} via ENV (line {lineno})"
                )
        demo_values.extend(_env_flag_values(rest, _DEMO_KEY_NAME))
    saw_one = False
    for val in demo_values:
        if val == "1":
            saw_one = True
        else:
            problems.append(
                f"{DEMO_KEYS_IN_IMAGE}: {rel} sets {_DEMO_KEY_NAME} "
                f"to {_demo_value_shown(val)} via ENV"
            )
    if require_demo_key and not saw_one and not demo_values:
        problems.append(
            f"{DEMO_KEYS_IN_IMAGE}: {rel} does not set {_DEMO_KEY_NAME}=1"
        )
    return problems


def _append_bad_demo(problems: list[str], rel: str, where: str, value: object) -> None:
    if _demo_value_allowed(value):
        return
    problems.append(
        f"{DEMO_KEYS_IN_IMAGE}: {rel} sets {_DEMO_KEY_NAME} "
        f"to {_demo_value_shown(value)} {where}"
    )


def _scan_compose(root: Path, path: Path, tracked: set[str] | None) -> list[str]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return []
    if "services:" not in text and "services :" not in text:
        return []
    try:
        docs = list(yaml.safe_load_all(text))
    except yaml.YAMLError:
        problems: list[str] = []
        for flag, finding, assign in _IMAGE_ENV_FLAGS:
            if _text_assigns_flag(text, assign):
                problems.append(
                    f"{finding}: {_rel(root, path)} sets {flag} "
                    "(compose YAML did not parse)"
                )
        rel = _rel(root, path)
        for bad in _env_text_bad_demo_values(text):
            problems.append(
                f"{DEMO_KEYS_IN_IMAGE}: {rel} sets {_DEMO_KEY_NAME} "
                f"to {bad} (compose YAML did not parse)"
            )
        return problems
    if not any(_has_services(doc) for doc in docs):
        return []
    rel = _rel(root, path)
    problems = []
    for doc in docs:
        if not isinstance(doc, dict):
            continue
        for flag, finding, assign in _IMAGE_ENV_FLAGS:
            if _environment_sets_flag(doc.get("environment"), flag):
                problems.append(f"{finding}: {rel} sets {flag} via environment")
            for entry in _env_file_entries(doc.get("env_file")):
                env_path = _env_path_if_scannable(root, path, entry, tracked)
                if env_path is not None and _file_assigns_flag(env_path, assign):
                    problems.append(
                        f"{finding}: {rel} sets {flag} via env_file {entry}"
                    )
            for name, spec in _iter_services(doc):
                if _environment_sets_flag(spec.get("environment"), flag):
                    problems.append(
                        f"{finding}: {rel} service {name} sets {flag} via environment"
                    )
                for entry in _env_file_entries(spec.get("env_file")):
                    env_path = _env_path_if_scannable(root, path, entry, tracked)
                    if env_path is not None and _file_assigns_flag(env_path, assign):
                        problems.append(
                            f"{finding}: {rel} service {name} sets "
                            f"{flag} via env_file {entry}"
                        )
        for value in _environment_demo_values(doc.get("environment")):
            _append_bad_demo(problems, rel, "via environment", value)
        for entry in _env_file_entries(doc.get("env_file")):
            env_path = _env_path_if_scannable(root, path, entry, tracked)
            if env_path is None:
                continue
            for bad in _file_bad_demo_values(env_path):
                problems.append(
                    f"{DEMO_KEYS_IN_IMAGE}: {rel} sets {_DEMO_KEY_NAME} "
                    f"to {bad} via env_file {entry}"
                )
        for name, spec in _iter_services(doc):
            for value in _environment_demo_values(spec.get("environment")):
                if _demo_value_allowed(value):
                    continue
                problems.append(
                    f"{DEMO_KEYS_IN_IMAGE}: {rel} service {name} sets "
                    f"{_DEMO_KEY_NAME} to {_demo_value_shown(value)} via environment"
                )
            for entry in _env_file_entries(spec.get("env_file")):
                env_path = _env_path_if_scannable(root, path, entry, tracked)
                if env_path is None:
                    continue
                for bad in _file_bad_demo_values(env_path):
                    problems.append(
                        f"{DEMO_KEYS_IN_IMAGE}: {rel} service {name} sets "
                        f"{_DEMO_KEY_NAME} to {bad} via env_file {entry}"
                    )
    return problems


def scan_dms_auth_disabled(root: Path) -> list[str]:
    """Image-auth findings for Dockerfiles and compose files.

    ``AUTH_DISABLED_IN_IMAGE`` when ``DMS_AUTH_DISABLED`` is set.
    ``DEV_MODE_IN_IMAGE`` when ``CORTEX_DEV_MODE`` is set.

    ``docker-compose.dev.yml`` is tracked and scanned like any other compose
    file. It does not assign ``CORTEX_DEV_MODE``. Its ``env_file`` target is
    gitignored, so it is not in ``git ls-files`` and is never read. ``env_file``
    is applied when the container runs and is not baked into the image, which
    is why a named exemption was considered and rejected.

    The full-tree walk runs only when ``root`` has no ``.git``. A real repo
    whose ``git ls-files`` fails stops with ``GIT_LS_FILES_FAILED``.
    """
    tracked, err = _git_tracked(root)
    if err:
        return [f"{DEV_MODE_IN_IMAGE}: {err}"]
    problems: list[str] = []
    if tracked is None:
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [
                d for d in dirnames if d not in _SKIP_DIRS and not d.endswith(".egg-info")
            ]
            for name in filenames:
                path = Path(dirpath) / name
                if _is_dockerfile(name):
                    problems.extend(_scan_dockerfile(root, path, require_demo_key=False))
                elif name.endswith((".yml", ".yaml")):
                    problems.extend(_scan_compose(root, path, None))
        return problems
    for rel in sorted(tracked):
        path = root / rel
        name = path.name
        if _is_dockerfile(name):
            problems.extend(
                _scan_dockerfile(
                    root,
                    path,
                    require_demo_key=rel in _SHIPPED_DOCKERFILES,
                )
            )
        elif name.endswith((".yml", ".yaml")):
            problems.extend(_scan_compose(root, path, tracked))
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
            f"{len(DOCKERFILES)} Dockerfiles, denylist clean, "
            "no DMS_AUTH_DISABLED or CORTEX_DEV_MODE in images"
        )
    else:
        print(
            "supply chain OK: no DMS_AUTH_DISABLED or CORTEX_DEV_MODE "
            "in Dockerfile or compose files"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
