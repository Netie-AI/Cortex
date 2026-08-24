# DISTILL.md — Netie Engine Skill Distillation Trace

**Always start and end here.** Every Claude / Cursor / Claude Code / Cowork action worth keeping must leave a trail that points back to this file.

| Field | Value |
|-------|-------|
| Root | `skill_distill/` |
| Policy | Capture → Normalize → Learn → Promote (rules/skills/subagents) → Parking-lot if deferred |
| Owners | Netie engine (Cortex) builders |
| Last process | 2026-08-24T05:40:00Z (outreach/voice skill pass-down capture) |

---

## Why this exists

Cortex/Netie must orchestrate as strongly as Claude Code + Cursor combined:
one-shot plans, multitasking, subagents, MCP/connectors, skills, memory/RAG,
cloud agents, deploy/scale. We **distill** how those products actually work
by asking them structured questions, storing answers, and folding survivors
into engine design — never cargo-culting UI labels without a capture.

---

## Directory map

```
skill_distill/
  DISTILL.md                 ← you are here (trace index)
  README.md                  ← how to run a distill session
  prompts/
    ASK_CLAUDE_CODE.md       ← paste into Claude Code
    ASK_CURSOR.md            ← paste into Cursor Agent
    ASK_CLAUDE_APP.md        ← paste into claude.ai / Cowork
    MASTER_INTERROGATION.md  ← full question bank (all topics)
  captures/                  ← raw answers (one file per session)
    _TEMPLATE.md
    YYYY-MM-DD_<source>_<topic>.md
  learned/                   ← normalized facts + engine implications
    INDEX.md
    memory_layer.md
    tool_orchestration.md
    claude_code_vs_cursor.md
    cloud_agents_scale.md
    …
  sources/                   ← screenshots, changelogs, URLs cited
  SUBAGENT_DISTILL.md        ← how to invoke the distill subagent
```

---

## Capture → promote loop

1. **Ask** — open `prompts/MASTER_INTERROGATION.md` (or a focused ASK_* file).
2. **Run** — Claude Code one-shot / Cursor Agent / Claude app. Prefer multitask
   mode for parallel topic lanes (memory | tools | cloud | scale).
3. **Store** — save the full answer under `captures/` using `_TEMPLATE.md`.
4. **Ingest** — `python scripts/distill_ingest.py [--capture PATH]`
   - updates `learned/INDEX.md`
   - appends deferred items to `PARKING_LOT.md` (P19)
   - refreshes the “Last process” line in this file
5. **Promote** — anything marked `promote: rule|skill|subagent` lands in:
   - Cursor user rules (global)
   - `.cursor/rules/`, `.cursor/skills/`, `.cursor/AGENTS.md`
   - Cortex `skills/*.yaml` / discovery refs when executable

---

## Trace-back rule (non-negotiable)

Every promoted rule, skill, or parking-lot item MUST cite:

```text
distill: skill_distill/captures/<file>.md#<heading>
```

If you cannot cite a capture, it is opinion — keep it out of production paths.

---

## Topic coverage checklist

Use this when starting a full distill sprint. Check boxes only after a capture exists.

### Claude product surface (from Capabilities UI)
- [x] Memory: search/reference chats
- [x] Memory: generate from history (legacy) vs project memory
- [x] Memory: import from other providers
- [x] Tool access mode: load when needed vs already loaded (compaction tradeoff)
- [x] Connector search (directory surfacing)
- [x] Skills vs Connectors vs Plugins (Customize)
- [x] Claude Code settings vs chat Capabilities
- [ ] Cowork / Claude in Chrome differences

### Claude Code agentic runtime
- [x] One-shot plan mode (plan → execute all tasks) _(live 2026-07-25 — `captures/2026-07-25_claude-code_all-lanes.md`)_
- [x] Multitasking / parallel agents _(live 2026-07-25 — all-lanes capture)_
- [x] Subagent spawn, isolation, tool allowlists _(live 2026-07-25 — all-lanes capture)_
- [x] Skills CLI (`find-skills`, `npx skills add`) _(marketplace/init path live-confirmed; `find-skills` itself still unconfirmed — see all-lanes UNKNOWNS + experiment E5)_
- [x] MCP / connectors load path _(live 2026-07-25 — all-lanes capture, incl. deferred ToolSearch loading)_
- [x] Hooks, permissions (Restrict / Allow / Request) _(live 2026-07-25 — all-lanes capture: deny/ask/allow + 6-step pipeline)_
- [x] Context compaction triggers _(live 2026-07-25 — all-lanes capture)_
- [x] Cloud agents: spin-up, deploy, scale, secrets _(live 2026-07-25 — all-lanes capture)_

### Cursor agentic runtime
- [x] Agent modes (Agent / Plan / Debug / Ask)
- [x] Task / subagent tool (`Task`, explore, shell, …)
- [x] MCP tool discovery (`GetMcpTools` / `CallMcpTool`)
- [x] Cloud agents vs local
- [x] Multitask Mode background agents _(Task run_in_background; Multitask Mode UI not fully verified)_
- [x] Rules (user / project), skills, AGENTS.md
- [x] Browser / Playwright automation path
- [x] Model routing / Composer vs frontier _(live 2026-07-25 — `captures/2026-07-25_cursor_model-routing-multitask.md`)_
- [x] Cortex as the loop (connectors: new-task Cursor chat, normal chat = chatbot) _(2026-08-22 — `captures/2026-08-22_cursor_orchestration-outside-editor.md`)_

### Netie engine mapping (must answer for each topic)
- [x] What Cortex already has
- [x] What to copy vs what to invert (governance-first)
- [x] Parking-lot vs build-now
- [x] Test / Playwright / stress gate required?

---

## Seed observations (2026-07-24)

Captured from Claude **Settings → Capabilities** screenshot (stored under
`sources/claude_capabilities_2026-07-24.md`):

| Setting | Observed | Distill implication for Netie |
|---------|----------|-------------------------------|
| Search and reference chats | ON | Chat-RAG over thread store; Cortex already has memory routes — map explicitly |
| Generate memory from chat history (Legacy) | ON | Separate “legacy memory” vs project memory; avoid one blob |
| Import memory from other AI providers | Start import | Need import adapters (ChatGPT/etc.) behind governance |
| Tool access mode | **Load tools when needed** | Prefer lazy tool catalogs (MCP find_*); reduce context pressure — aligns with Cortex discovery |
| Tools already loaded | alt option | Preload = more compaction; use only for small fixed toolsets |
| Customize: Skills / Connectors / Plugins | sidebar | Three layers — map to SkillCards / MCP / pack plugins |
| Claude Code / Cowork / Chrome | separate nav | Product surfaces ≠ one runtime; Netie should expose one engine, many skins |

Full interrogation still required — see prompts. These seeds are **hypotheses** until confirmed by ASK_* captures.

---

## Quick commands

```powershell
# New capture file
Copy-Item skill_distill\captures\_TEMPLATE.md skill_distill\captures\$(Get-Date -Format yyyy-MM-dd)_cursor_tools.md

# Ingest all new captures
python scripts\distill_ingest.py

# Ingest one file
python scripts\distill_ingest.py --capture skill_distill\captures\2026-07-24_claude_capabilities.md
```

---

## Related

- `PARKING_LOT.md` → **P19** Skill distill continuous learning
- `docs/discovery/FIND_SKILLS.md` — Find Skills tool
- `docs/strategy/ENGINE_SDK_DUAL_BRAIN_PLAN_2026-07-23.md` — dual brain
- `.cursor/AGENTS.md` — distill-subagent invoke
