"""SkillOpt evolution adapter — soft-fail when skillopt is not installed.

SkillOpt treats a skill document as trainable state for a frozen agent and
emits a validation-gated best_skill.md. Cortex wires that as an optional
post-discovery evolve step; discovery itself never requires the package.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from CortexOS.discovery.models import DiscoveryHit
from CortexOS.paths import data_path

DEFAULT_OUT = data_path("discovery", "skillopt")


def skillopt_available() -> bool:
    try:
        import skillopt  # noqa: F401
    except ImportError:
        return False
    return True


def evolve_hit(
    hit: DiscoveryHit,
    *,
    goal: str,
    out_dir: Path | None = None,
    enable: bool = True,
) -> dict[str, Any]:
    """Attempt SkillOpt-style evolve for a matched skill.

    Without the skillopt package, writes a seed skill markdown that can be
    trained later (`skillopt` CLI / SkillOpt-Sleep) and marks evolved=False.
    """
    if not enable:
        return {"ok": False, "skipped": True, "reason": "evolve_disabled"}

    root = out_dir or DEFAULT_OUT
    root.mkdir(parents=True, exist_ok=True)
    safe = "".join(c if c.isalnum() or c in "-_" else "-" for c in hit.name)[:64] or "skill"
    skill_path = root / f"{safe}.md"
    best_path = root / f"{safe}.best_skill.md"

    seed = _seed_markdown(hit, goal=goal)
    skill_path.write_text(seed, encoding="utf-8")

    meta: dict[str, Any] = {
        "ok": True,
        "skill_path": str(skill_path),
        "best_skill_path": str(best_path),
        "skillopt_installed": skillopt_available(),
        "evolved": False,
        "goal": goal,
        "hit_id": hit.id,
    }

    if not skillopt_available():
        # Offline stub: copy seed as candidate best_skill so callers have an artifact.
        best_path.write_text(seed + "\n\n<!-- pending SkillOpt validation gate -->\n", encoding="utf-8")
        meta["reason"] = "skillopt_not_installed; wrote seed for later training"
        meta["install_hint"] = "pip install skillopt"
        (root / f"{safe}.meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
        return meta

    # Optional live path — keep conservative: do not run full training loops
    # inside a tool call. Record that SkillOpt is present and seed is ready.
    try:
        import skillopt  # noqa: F401

        best_path.write_text(
            seed
            + "\n\n<!-- skillopt present: run `skillopt` / `skillopt-sleep` offline to gate updates -->\n",
            encoding="utf-8",
        )
        meta["evolved"] = False
        meta["reason"] = "skillopt_present; seed ready for offline train/sleep"
        meta["cli"] = ["skillopt", "skillopt-sleep"]
    except Exception as exc:  # noqa: BLE001
        meta["ok"] = False
        meta["error"] = str(exc)[:200]

    (root / f"{safe}.meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    return meta


def _seed_markdown(hit: DiscoveryHit, *, goal: str) -> str:
    return (
        f"# {hit.name}\n\n"
        f"> Goal: {goal}\n\n"
        f"{hit.description}\n\n"
        f"## Source\n\n"
        f"- id: `{hit.id}`\n"
        f"- url: {hit.url or '(local)'}\n"
        f"- source: {hit.source}\n"
        f"- install: `{hit.install_hint}`\n\n"
        f"## When to use\n\n"
        f"Use this skill when the user asks for help with: {goal}.\n\n"
        f"## Procedure\n\n"
        f"1. Confirm the goal and constraints.\n"
        f"2. Follow the upstream skill / MCP instructions at the source URL.\n"
        f"3. Prefer local Cortex SkillCards when reputation=local.\n"
        f"4. After successful runs, evolve this document with SkillOpt behind a held-out gate.\n"
    )
