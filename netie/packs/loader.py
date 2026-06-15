"""Load vertical pack assets (rules, agents, skills) from ``packs/{name}/``."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def resolve_pack_dir(pack_dir: Path | str) -> Path:
    path = Path(pack_dir)
    if path.is_absolute():
        return path
    return repo_root() / path


@dataclass(frozen=True, slots=True)
class PackConfig:
    name: str
    rules_dir: Path
    agents_dir: Path
    skills_dir: Path


def load_pack(pack_name: str, pack_dir: Path | str) -> PackConfig:
    """Loads rules, agents, skills from ``packs/{pack_name}/``."""
    root = resolve_pack_dir(pack_dir) / pack_name
    return PackConfig(
        name=pack_name,
        rules_dir=root / "rules",
        agents_dir=root / "agents",
        skills_dir=root / "skills",
    )
