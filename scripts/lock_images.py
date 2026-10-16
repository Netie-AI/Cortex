#!/usr/bin/env python3
"""Regenerate the hash-locked image requirements (HX-02, R13.1).

Each shipped image installs ``requirements/image-<variant>.lock.txt`` with
``pip install --require-hashes``, then installs the project itself with
``--no-deps``. These locks are produced here by ``uv pip compile
--generate-hashes`` and never edited by hand; ``scripts/check_supply_chain.py``
fails the build when one goes stale.

Target: CPython 3.11 on x86_64 Linux (``python:3.11-slim``), wheels only, so no
sdist build can fetch an unhashed build backend. ``requirements/build.in`` adds
the project's build backend so the ``--no-deps --no-build-isolation`` install of
the project uses a hashed ``poetry-core`` too. An existing output file keeps
its pins. To move one package, pass ``--upgrade-package <name>`` (repeatable).
There is no blanket ``--upgrade``: that floats every pin in the file.

``--align-uv-lock`` is the other way to move pins. It constrains every package
``uv.lock`` resolves for the image target (the Dockerfiles' CPython on Linux
x86_64) to that exact version, and recompiles only the shared packages whose
image pin differs. A constraint does not add a package and does not drop an
extra. Packages that are not in ``uv.lock`` keep their pins. It does not take
the newest version on the index.

Needs network access to the package index. Usage::

    python scripts/lock_images.py            # all variants, pins kept
    python scripts/lock_images.py core full  # a subset
    python scripts/lock_images.py --upgrade-package litellm
    python scripts/lock_images.py --align-uv-lock
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

#: variant -> project extras that image installs today. ``constructor`` is the
#: repo-root ``Dockerfile`` (an identical copy of ``Dockerfile.constructor``).
VARIANTS: dict[str, tuple[str, ...]] = {
    "constructor": ("dms",),
    "core": (),
    "full": ("full",),
}


def lock_path(variant: str) -> Path:
    return ROOT / "requirements" / f"image-{variant}.lock.txt"


def compile_command(
    variant: str,
    upgrade_packages: tuple[str, ...] = (),
    constraints: Path | None = None,
) -> list[str]:
    cmd = [
        "uv", "pip", "compile", "pyproject.toml", "requirements/build.in",
        "--generate-hashes",
        "--only-binary", ":all:",
        "--python-version", "3.11",
        "--python-platform", "x86_64-unknown-linux-gnu",
        "--no-header",
        "--quiet",
        "--output-file", str(lock_path(variant).relative_to(ROOT)),
    ]
    if constraints is not None:
        cmd += ["--constraints", str(constraints)]
    for name in upgrade_packages:
        cmd += ["--upgrade-package", name]
    for extra in VARIANTS[variant]:
        cmd += ["--extra", extra]
    return cmd


def parse_args(argv: list[str]) -> tuple[list[str], tuple[str, ...], bool]:
    """Split variant names, ``--upgrade-package``, and ``--align-uv-lock``."""
    variants: list[str] = []
    upgrade: list[str] = []
    align = False
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg == "--upgrade-package":
            if i + 1 >= len(argv):
                raise SystemExit("--upgrade-package needs a package name")
            upgrade.append(argv[i + 1])
            i += 2
            continue
        if arg == "--align-uv-lock":
            align = True
            i += 1
            continue
        variants.append(arg)
        i += 1
    return variants, tuple(upgrade), align


def _resolver():
    """The supply-chain resolver, importable as a script or as ``scripts.*``."""
    root_s = str(ROOT)
    if root_s not in sys.path:
        sys.path.insert(0, root_s)
    from scripts.check_supply_chain import (
        parse_lock,
        resolve_image_target,
        uv_resolution_for_image,
    )

    return parse_lock, resolve_image_target, uv_resolution_for_image


def uv_lock_constraint_pins(root: Path = ROOT) -> dict[str, str]:
    """Exact ``uv.lock`` versions selected for the image target.

    A package with zero or several versions for that target is a mismatch the
    image lock cannot follow. Callers stop instead of picking one.
    """
    _parse_lock, resolve_image_target, uv_resolution_for_image = _resolver()
    version, problems = resolve_image_target(root)
    if version is None or problems:
        raise SystemExit("\n".join(problems) or "image target is unset")
    resolved = uv_resolution_for_image(root, version)
    pins: dict[str, str] = {}
    bad: list[str] = []
    for name, versions in sorted(resolved.items()):
        if len(versions) != 1:
            shown = ", ".join(sorted(versions)) if versions else "none"
            bad.append(f"{name} ({shown})")
            continue
        pins[name] = next(iter(versions))
    if bad:
        raise SystemExit(
            "uv.lock has no single image-target version for: " + ", ".join(bad)
        )
    return pins


def packages_to_realign(
    variant: str, constraint_pins: dict[str, str], root: Path = ROOT
) -> tuple[str, ...]:
    """Shared packages whose image pin is not the ``uv.lock`` version."""
    parse_lock, _resolve, _uv = _resolver()
    path = root / "requirements" / f"image-{variant}.lock.txt"
    pins, problems = parse_lock(path)
    if problems:
        raise SystemExit("\n".join(problems))
    return tuple(
        name
        for name in sorted(pins)
        if name in constraint_pins and pins[name] != constraint_pins[name]
    )


def main(argv: list[str]) -> int:
    requested, upgrade_packages, align = parse_args(argv)
    variants = requested or list(VARIANTS)
    unknown = [v for v in variants if v not in VARIANTS]
    if unknown:
        print(f"unknown variant(s): {', '.join(unknown)}; known: {', '.join(VARIANTS)}")
        return 2
    constraints_path: Path | None = None
    constraint_pins: dict[str, str] = {}
    if align:
        constraint_pins = uv_lock_constraint_pins(ROOT)
        fd, raw = tempfile.mkstemp(prefix="uv-lock-constraints-", suffix=".txt")
        constraints_path = Path(raw)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            for name, version in constraint_pins.items():
                handle.write(f"{name}=={version}\n")
    try:
        for variant in variants:
            upgrades = list(upgrade_packages)
            if align:
                upgrades.extend(packages_to_realign(variant, constraint_pins))
            cmd = compile_command(variant, tuple(dict.fromkeys(upgrades)), constraints_path)
            print("+", " ".join(cmd))
            rc = subprocess.run(cmd, cwd=ROOT).returncode
            if rc != 0:
                return rc
            if align:
                # The constraints file is a temp path. A second compile keeps
                # those pins and rewrites the via comments without it.
                cmd = compile_command(variant)
                print("+", " ".join(cmd))
                rc = subprocess.run(cmd, cwd=ROOT).returncode
                if rc != 0:
                    return rc
    finally:
        if constraints_path is not None:
            constraints_path.unlink(missing_ok=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
