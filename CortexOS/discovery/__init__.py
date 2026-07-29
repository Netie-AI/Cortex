"""Cortex discovery — find skills / MCP / subagents from curated GitHub refs.

Policy when Cortex needs capability:
  1. Search local SkillCards + reference skill catalogs (skills first)
  2. Search MCP / subagent catalogs if skills are insufficient
  3. Optionally evolve a matched skill with SkillOpt (validation-gated)

Exposes the Find Skills tool surface used by MCP, workflow brokers, and HTTP.
"""

from CortexOS.discovery.find import (
    find_mcp,
    find_skills,
    find_subagents,
    discover_for_goal,
)

__all__ = [
    "find_skills",
    "find_mcp",
    "find_subagents",
    "discover_for_goal",
]
