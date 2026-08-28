# Cursor Agent Roles (Cortex / Netie)

See also root `AGENTS.md` and `skill_distill/DISTILL.md`.

## Quick invokes

| Say this | Subagent / skill |
|----------|------------------|
| `Use dms-subagent-dispatch to ship the next feature` | DMS feature builder |
| `Run distill-session against Claude Code` | distill-session |
| `Distill Cursor multitask + MCP into skill_distill` | distill-session |
| `Ingest distill captures` | shell: `python scripts/distill_ingest.py` |
| `Gate the Netie-AI estate before shipping` | crew-ship-gate |

---

## distill-session

**Purpose:** Interrogate Claude Code / Cursor / Claude.app about agentic internals
(memory, tools, skills, MCP, one-shot plan, multitask, cloud scale) and **store**
answers under `skill_distill/captures/`, then ingest.

**Reads first:** `skill_distill/DISTILL.md`, `skill_distill/prompts/MASTER_INTERROGATION.md`

**Steps:**
1. Pick unchecked topics from DISTILL.md checklist.
2. Open the matching ASK_* prompt; run asks (prefer parallel Task lanes).
3. Write capture files from `_TEMPLATE.md`.
4. Run `python scripts/distill_ingest.py`.
5. Promote `promote: rule|skill|subagent` items; park the rest under PARKING_LOT P19 with `distill:` cites.
6. Update DISTILL.md checklist only for topics that now have captures.

**Never:** invent product internals without `evidence:`; skip storing raw answers.

---

## dms-subagent-dispatch

Builder for DMS gates. Follow `CURSOR_HANDOFF.md` → `STATUS.md` → BUILD_PLAN.
After meaningful orchestration learnings mid-build, spawn **distill-session** to capture them.
