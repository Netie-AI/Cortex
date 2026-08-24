"""Repo-anchored paths — never rely on process cwd for data/*.

Uvicorn / OpenVault launchers often start Cortex with a non-repo cwd; relative
``Path("data")`` then hits WinError 433 (device does not exist) on Windows.
"""

from __future__ import annotations

import os
from pathlib import Path


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def data_path(*parts: str | Path) -> Path:
    return repo_root().joinpath("data", *map(str, parts))


def constructor_skin_dir() -> Path:
    """Constructor HTML/JS. Env override for laptop live-reload; else the vendored skin."""
    env = (os.environ.get("CONSTRUCTOR_SKIN_DIR") or "").strip()
    if env:
        return Path(env)
    return Path(__file__).resolve().parent / "constructor_skin"
