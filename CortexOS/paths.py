"""Repo-anchored paths — never rely on process cwd for data/*.

Uvicorn / OpenVault launchers often start Cortex with a non-repo cwd; relative
``Path("data")`` then hits WinError 433 (device does not exist) on Windows.
"""

from __future__ import annotations

from pathlib import Path


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def data_path(*parts: str | Path) -> Path:
    return repo_root().joinpath("data", *map(str, parts))
