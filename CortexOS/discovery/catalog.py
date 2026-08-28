"""Load offline reference catalogs + local SkillCards."""

from __future__ import annotations

import json
from collections.abc import Iterable
from functools import lru_cache
from pathlib import Path

from CortexOS.discovery.models import RefItem

REFS_DIR = Path(__file__).resolve().parent / "refs"


def _repo_skills_dir() -> Path:
    return Path(__file__).resolve().parents[2] / "skills"


def _load_json_items(path: Path) -> list[dict]:
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, dict):
        return list(data.get("items") or [])
    if isinstance(data, list):
        return data
    return []


def load_sources() -> dict:
    path = REFS_DIR / "sources.json"
    if not path.exists():
        return {"updated": "", "policy": "skills_first_then_mcp; evolve_with_skillopt", "sources": []}
    return json.loads(path.read_text(encoding="utf-8"))


def load_ref_items(*, kinds: Iterable[str] | None = None) -> list[RefItem]:
    kind_set = set(kinds) if kinds else None
    files = {
        "skill": REFS_DIR / "skills.json",
        "mcp": REFS_DIR / "mcp_servers.json",
        "subagent": REFS_DIR / "subagents.json",
    }
    items: list[RefItem] = []
    for kind, path in files.items():
        if kind_set is not None and kind not in kind_set and not (
            kind == "subagent" and kind_set & {"subagent", "toolkit"}
        ):
            continue
        for raw in _load_json_items(path):
            try:
                item = RefItem(**raw)
            except Exception:
                continue
            if kind_set is not None and item.kind not in kind_set:
                continue
            items.append(item)
        if kind == "mcp":
            for raw in _load_json_items(REFS_DIR / "cortex_mcp.json"):
                try:
                    item = RefItem(**raw)
                except Exception:
                    continue
                items.append(item)
    return items


def load_local_skill_items(skills_dir: Path | None = None) -> list[RefItem]:
    """Map repo SkillCards into RefItems for unified ranking."""
    from CortexOS.fabrication.skill_registry import load_skill_cards

    directory = skills_dir or _repo_skills_dir()
    items: list[RefItem] = []
    for card in load_skill_cards(directory):
        items.append(
            RefItem(
                id=f"local_skill:{card.skill_id}",
                kind="local_skill",
                name=card.name,
                description=card.description,
                url="",
                source="cortex/skills",
                category=card.category,
                tags=list(card.required_tools),
                install_hint=f"Already installed as SkillCard {card.skill_id}",
                reputation="local",
            )
        )
    return items


@lru_cache(maxsize=4)
def catalog_snapshot(include_local: bool = True) -> tuple[RefItem, ...]:
    items = load_ref_items()
    if include_local:
        items = load_local_skill_items() + items
    return tuple(items)


def clear_catalog_cache() -> None:
    catalog_snapshot.cache_clear()
