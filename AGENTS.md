# Cortex DMS — Agent Roles

See `.cursor/AGENTS.md` for full subagent definitions.

**Start every session:** [`docs/ACTIVE.md`](docs/ACTIVE.md) (engine-first).  
DMS lane: [`docs/dms/ACTIVE.md`](docs/dms/ACTIVE.md). Consumers: [`docs/engine/CONSUMERS.md`](docs/engine/CONSUMERS.md).  
Archived PASS packets: [`docs/bin/`](docs/bin/).

**Quick invoke:**
- `Use dms-subagent-dispatch to ship the next feature`
- `Run distill-session` — capture Claude Code / Cursor agentic internals → `skill_distill/`
- Trace root: `skill_distill/DISTILL.md`

**Historical spine (shipped):** F1 → F2 → F3 → F4 → F5 → F6 → F7 → F8 host-shim slice · V0  
**Current product spine:** Postgres Phase0 → Amend → Spaces · eval N→310 · C5→C8 (P22)  
**Gate between milestones:** `docs/dms/SUPERVISOR_GATE.md`  
**Sandbox truth:** `docs/dms/SANDBOX_ORIENTATION.md` (host tools + Docker apps; WASM = P2)
