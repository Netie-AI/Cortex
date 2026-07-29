"""Public find_skills / find_mcp / find_subagents entrypoints."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from CortexOS.discovery.catalog import catalog_snapshot, clear_catalog_cache, load_sources
from CortexOS.discovery.models import DiscoveryResult
from CortexOS.discovery.search import CatalogIndex
from CortexOS.discovery.skillopt_evolve import evolve_hit

_QUESTION = "Are there any good skills for [{goal}]?"


def find_skills(
    goal: str,
    *,
    top_k: int = 8,
    evolve: bool = False,
    out_dir: Path | None = None,
) -> dict[str, Any]:
    """Answer: Are there any good skills for [GOAL]?

    Searches local SkillCards first (via catalog), then GitHub awesome-list refs.
    Optionally seeds SkillOpt evolution for the best match.
    """
    return discover_for_goal(
        goal,
        kinds={"local_skill", "skill"},
        top_k=top_k,
        evolve=evolve,
        out_dir=out_dir,
        question_template=_QUESTION,
    ).as_dict()


def find_mcp(goal: str, *, top_k: int = 8) -> dict[str, Any]:
    return discover_for_goal(goal, kinds={"mcp"}, top_k=top_k, evolve=False).as_dict()


def find_subagents(goal: str, *, top_k: int = 8) -> dict[str, Any]:
    return discover_for_goal(
        goal,
        kinds={"subagent", "toolkit"},
        top_k=top_k,
        evolve=False,
    ).as_dict()


def discover_for_goal(
    goal: str,
    *,
    kinds: set[str] | None = None,
    top_k: int = 8,
    evolve: bool = False,
    out_dir: Path | None = None,
    question_template: str = _QUESTION,
) -> DiscoveryResult:
    g = (goal or "").strip()
    sources = load_sources()
    if not g:
        return DiscoveryResult(
            ok=False,
            goal="",
            question=question_template.format(goal="GOAL"),
            matches=[],
            sources_consulted=[s.get("id", "") for s in sources.get("sources", [])],
            hint="Provide a goal, e.g. find_skills('playwright e2e testing')",
        )

    # Skills-first: if kinds omitted, rank all but prefer skills via scoring.
    items = list(catalog_snapshot(include_local=True))
    index = CatalogIndex(items)
    matches = index.query(g, top_k=top_k, kinds=kinds)

    # If caller asked for skills and nothing strong, widen to mcp/subagents once.
    if kinds and kinds <= {"local_skill", "skill"} and (not matches or matches[0].score < 1.0):
        widened = index.query(g, top_k=top_k, kinds=None)
        # Keep skill matches first, then append non-skill fillers.
        seen = {m.id for m in matches}
        for hit in widened:
            if hit.id in seen:
                continue
            matches.append(hit)
            seen.add(hit.id)
            if len(matches) >= top_k:
                break

    best = matches[0] if matches else None
    skillopt_meta: dict[str, Any] = {}
    if evolve and best is not None and best.kind in ("skill", "local_skill"):
        skillopt_meta = evolve_hit(best, goal=g, out_dir=out_dir, enable=True)
        if skillopt_meta.get("ok"):
            best = best.model_copy(
                update={
                    "evolved": bool(skillopt_meta.get("evolved")),
                    "skillopt_path": str(skillopt_meta.get("best_skill_path") or ""),
                }
            )
            matches[0] = best

    return DiscoveryResult(
        ok=True,
        goal=g,
        question=question_template.format(goal=g),
        matches=matches,
        best=best,
        sources_consulted=[s.get("id", "") for s in sources.get("sources", [])],
        skillopt=skillopt_meta,
        policy=str(sources.get("policy") or "skills_first_then_mcp; evolve_with_skillopt"),
    )


def refresh_catalog_cache() -> None:
    clear_catalog_cache()


# Tool schemas for MCP / agent broker -----------------------------------------

FIND_SKILLS_SCHEMA = {
    "name": "find_skills",
    "description": (
        "Find the best agent skills for a goal (skills first, then MCP/subagents). "
        "Equivalent to asking: 'Are there any good skills for [GOAL]?' "
        "Uses curated GitHub awesome-list references and local SkillCards; "
        "optionally seeds SkillOpt evolution."
    ),
    "params": {
        "goal": "string — what you need to accomplish",
        "top_k": "int — max matches (default 8)",
        "evolve": "bool — seed SkillOpt evolve for best match (default false)",
    },
}

FIND_MCP_SCHEMA = {
    "name": "find_mcp",
    "description": "Find MCP servers from the awesome-mcp-servers reference catalog for a goal.",
    "params": {
        "goal": "string",
        "top_k": "int — default 8",
    },
}

FIND_SUBAGENTS_SCHEMA = {
    "name": "find_subagents",
    "description": "Find subagents / toolkit entries from awesome Claude Code toolkit references.",
    "params": {
        "goal": "string",
        "top_k": "int — default 8",
    },
}

DISCOVERY_TOOLS = {
    "find_skills": lambda **kw: find_skills(
        str(kw.get("goal") or kw.get("query") or ""),
        top_k=int(kw.get("top_k") or 8),
        evolve=bool(kw.get("evolve") or False),
    ),
    "find_mcp": lambda **kw: find_mcp(
        str(kw.get("goal") or kw.get("query") or ""),
        top_k=int(kw.get("top_k") or 8),
    ),
    "find_subagents": lambda **kw: find_subagents(
        str(kw.get("goal") or kw.get("query") or ""),
        top_k=int(kw.get("top_k") or 8),
    ),
}

SCHEMAS = [FIND_SKILLS_SCHEMA, FIND_MCP_SCHEMA, FIND_SUBAGENTS_SCHEMA]
