---
name: distill-session
description: >
  Run a Netie skill-distill session: ask Claude Code/Cursor structured questions
  about agentic architecture, store captures under skill_distill/, ingest, and
  update PARKING_LOT P19 / rules / skills. Use when user says distill, Claude Code
  internals, how Cursor agents work, multitask, cloud agents, memory layer, or
  tool orchestration.
---

# Distill Session Skill

## When to use
User wants Netie to match Claude Code / Cursor orchestration strength, or asks
how those products implement memory, RAG, tools, skills, MCP, subagents, plan
mode, multitask, or cloud scale.

## Workflow
1. Read `skill_distill/DISTILL.md` (trace root).
2. Choose prompt:
   - Claude Code → `skill_distill/prompts/ASK_CLAUDE_CODE.md`
   - Cursor → `skill_distill/prompts/ASK_CURSOR.md`
   - Claude.app Capabilities → `skill_distill/prompts/ASK_CLAUDE_APP.md`
   - Full bank → `MASTER_INTERROGATION.md`
3. Prefer **multitask**: memory | tools | agents | cloud.
4. Save answers to `skill_distill/captures/YYYY-MM-DD_<source>_<topic>.md`.
5. `python scripts/distill_ingest.py --capture <path>`
6. Promote or park with `distill:` citations.

## Output checklist
- [ ] Capture file exists
- [ ] learned/INDEX updated (via ingest)
- [ ] PARKING_LOT P19 updated if deferred
- [ ] DISTILL.md checklist boxes only if capture exists
