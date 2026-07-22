---
name: dms-subagent-dispatch
description: Dispatches the correct Cursor subagent for the next DMS build step. Use when user says invoke subagent, dispatch builder, run next feature, or automate DMS build sequence.
---

# DMS Subagent Dispatch

## Pick next work item

1. Read `CHANGELOG_DMS.md` — last completed feature.
2. Sequence: F1→F2→F3→F4→F5→F6→F7→V0→V1→V2→V3 (see `.cursor/AGENTS.md`).
3. Next = first not logged as complete.

## Dispatch

Use **Task** tool:

| Step | subagent_type | readonly |
|------|---------------|----------|
| Build feature | `generalPurpose` | false |
| Pre-plan explore | `explore` | true |
| Post-build gate | `generalPurpose` | true |

**Builder prompt (copy and fill [N]):**
```
You are dms-feature-builder for Netie Cortex.
Feature: [F1 | F2 | ... | V0 | ...]
Read: docs/dms/BUILD_PLAN.md (or VISION_GOVERNANCE.md for V*)
Rules: .cursor/rules/
Skill: .cursor/skills/dms-feature-ship/SKILL.md
ANTI-SCOPE is mandatory. One feature only. pytest green. Update CHANGELOG_DMS.md.
Repo: c:\Users\user\RUMA\Cortex
```

## After builder completes

Run **dms-claude-gate** skill before user proceeds to next feature.
