# cursor_orchestration (from 2026-07-25 distill-session)

**distill:** `skill_distill/captures/2026-07-25_cursor_distill-session.md`

## Facts worth keeping
1. Modes bound write access: Plan/Ask read-only; Agent writes; Debug for runtime evidence.
2. Task children are context-blind unless the parent prompt is complete.
3. MCP requires schema fetch before call; auth is a first-class state.
4. Lazy tool catalogs scale better than eager schema dumps.
5. Cloud agents need remote-reachable base branches.

## Netie build guidance
- Parent orchestrator must pack full briefs into AGENT_TASK / subagent prompts (already the template pattern).
- Keep `find_*` + small WEB_TOOLS as the default eager set; everything else on-demand.
- Do not pretend IDE SwitchMode exists in the engine — expose presets/race_router instead.
