---
name: dms-claude-gate
description: Prepares Claude supervisor gate verification packet after a DMS milestone. Use when user says gate verify, milestone check, Claude gate, or before starting next V-feature.
---

# DMS Claude Gate

## Steps

1. Read `docs/dms/SUPERVISOR_GATE.md` for the target gate (F1, V0, V1, …).
2. Read latest entries in `CHANGELOG_DMS.md`.
3. Run required tests; capture full output.
4. Produce paste-ready markdown using the template in SUPERVISOR_GATE.md.

## Output format

```markdown
## Gate: [ID]

### Shipped
[from CHANGELOG]

### Test output
[pytest output]

### Governance proof
- [x] or [ ] each checklist item

### Uncertain / needs review
- ...
```

Do not implement the next feature. Read-only verification only.
