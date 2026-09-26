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
the project uses a hashed ``poetry-core`` too.

Needs network access to the package index. Usage::

    python scripts/lock_images.py            # all variants
    python scripts/lock_images.py core full  # a subset
"""

from __future__ import annotations

import subprocess
import sys
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


def compile_command(variant: str) -> list[str]:
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
    for extra in VARIANTS[variant]:
        cmd += ["--extra", extra]
    return cmd


def main(argv: list[str]) -> int:
    variants = argv or list(VARIANTS)
    unknown = [v for v in variants if v not in VARIANTS]
    if unknown:
        print(f"unknown variant(s): {', '.join(unknown)}; known: {', '.join(VARIANTS)}")
        return 2
    for variant in variants:
        cmd = compile_command(variant)
        print("+", " ".join(cmd))
        rc = subprocess.run(cmd, cwd=ROOT).returncode
        if rc != 0:
            return rc
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
