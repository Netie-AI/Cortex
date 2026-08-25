---
id: 2026-08-23_crew_auto-detect-spawn
source: manual
date: 2026-08-23
operator: cursor-grok-4.6
prompt_used: skill_distill/DISTILL.md + learned/multi_agent_coordination.md + learned/tool_orchestration.md
distill_trace: skill_distill/DISTILL.md
status: normalized
---

# Crew auto-detect spawn (no fixed roster)

## Raw answer

HIT reuse, not a new interrogation of Claude Code/Cursor internals.

Copied patterns:
- Cursor Task: child gets a full brief; returns a summary; parent synthesizes. Crew `spawn_agent` + A2A already did this.
- Claude Code subagents: `tools` / `disallowedTools` frontmatter, final-message-only, sanitize injection. Crew now has `allow_tools` / `deny_tools` and sanitizes teammate A2A.
- Claude Code / Claude app: lazy tools, Skills vs Connectors vs Plugins. Crew shares the MCP pool and filters per agent.
- Anthropic: prefer single-agent; orchestrator-subagent; generator-verifier with explicit criteria and no rubber-stamp. Wired via `CortexOS/crew/detect.py` + existing `coordination_patterns.py`.
- GitHub/agentskills: markdown skills on disk, not AppData blobs.
- ChatGPT custom GPTs: skipped as a product clone. One Manager + job-named teammates.
- LangGraph: skipped. CLAUDE.md and distill say Cortex is the loop.

Skipped:
- Nested spawn depth product (teammates cannot spawn; Manager-only).
- Cloud VM parity / infinite Cursor chats.
- Reverse-engineering Grok Bot AppData.

## Extracted facts

| Fact | Evidence | Confidence | Promote |
|------|----------|------------|---------|
| Capabilities are templates, not roster buttons | UI chips are legend; detect keywords | high | skill |
| No spawn when operator forbids it | detect `_NO_SPAWN` | high | rule |
| Verifier requires explicit criteria | Anthropic GV + crew skip | high | rule |
| Shared tools + per-agent deny | Claude Code disallowedTools | high | skill |
| OpenVault holds keys; grok-4.6 not fast | crew config + store model rewrite | high | rule |

## Netie implications

- Build now: crew detect/spawn/verify/grants (this commit path)
- Park: Grok AppData decrypt; LangGraph; fusing Plane/Ticket Runner/Pointer
- Tests: `tests/test_crew/test_detect.py` + UI strings

## Citations

- distill: skill_distill/captures/2026-08-23_crew_auto-detect-spawn.md
- distill: skill_distill/captures/2026-07-27_anthropic_multi-agent-coordination.md
- distill: skill_distill/captures/2026-07-25_claude-code_all-lanes.md
- distill: skill_distill/learned/multi_agent_coordination.md
