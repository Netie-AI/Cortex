# Cortex DMS — Agent Roles

Use Cursor **Task** tool with these roles. **Sequential only** — one feature per subagent.

## dms-feature-builder

**When:** Implementing F1–F7 or V0–V3 from build plans.

**subagent_type:** `generalPurpose`

**Prompt template:**
```
Read docs/dms/BUILD_PLAN.md Feature [N] (or docs/dms/VISION_GOVERNANCE.md V[N]).
Follow .cursor/rules/*.mdc. ANTI-SCOPE is law.
Plan first → smallest diff → smoke tests → pytest -q green → append CHANGELOG_DMS.md.
Do NOT start the next feature.
```

## dms-explore

**When:** Investigating codebase before planning.

**subagent_type:** `explore`

**Prompt:** Read-only. Map files under CortexOS/dms/, packs/dms/, demo/dms-ui/. Return gaps vs BUILD_PLAN.

## dms-gate-verify

**When:** Milestone complete — prepare Claude gate packet.

**subagent_type:** `generalPurpose` with `readonly: true`

**Prompt:** Run tests listed in docs/dms/SUPERVISOR_GATE.md for gate [X]. Output paste-ready markdown per template. Do not write code.

## Sequencing (do not reorder)

```
F1 → F2 → F3 → F4 → F5 → F6 → F7 → V0 → V1 → V2 → V3
```

Dependencies: F4 needs F1+F3. F5 needs F4. F6 needs F5. V1 needs V0.

## Invoke via skill

Say: **"Use dms-subagent-dispatch to ship Feature F1"** — loads `.cursor/skills/dms-subagent-dispatch/SKILL.md`.

## Handoff files (read before every run)

- `STATUS.md` — gate, debt, next move
- `CONTEXT.md` — decisions + constraints
- `ARCHITECTURE.md` — built vs partial
- `PARKING_LOT.md` — do not build from here
- `python scripts/handoff.py` — clipboard block for Claude

## Parallel subagents (research only)

Use Task `explore` with `readonly: true` for parallel investigation. Never parallel ship of dependent features.
