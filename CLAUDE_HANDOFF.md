# CLAUDE_HANDOFF — Supervisor / Gate Session
**Auto-sync:** run `python scripts/handoff.py --write` after every ship or gate. Last generated: 2026-06-26T01:26:08+00:00
**Paste this entire file into a new Claude chat.**

---

## Your role
You are the **external supervisor**. You do NOT implement code. Verify gate packets and return PASS/FAIL.

## Current gate
| Field | Value |
|---|---|
| Last gates PASS | V0, V1, F1-hardened, F7, F2, F3-security, F4 |
| Gate pending | **F5** — compliance gate on tasks |
| Next build after PASS | **F6** skill capture (consented, opt-in) |

## Shipped this session (F5)
- `packs/dms/compliance/dms_rules_v1.yaml` — 3 field rules + Python value threshold
- `packs/dms/tasks/gate.py` — `check_task()`, `ComplianceVerdict`, ledger events
- `packs/dms/tasks/extract.py` — T2 extract only; rules decide verdict
- `packs/dms/sql/005_task_events_v0.sql`
- `CortexOS/api/task_routes.py` — `/dms/tasks/gate/check`, `/choose`, `/gate/acknowledge`
- PII-before-classify fix in `packs/dms/classify/intent.py`
- UI: chat verdict banner + brain gate inline; `demo/dms-ui/lib/api.js` restored

## Test snapshot
Run locally: `pytest -q` — **141 passed, 4 skipped**

## Gate F5 checklist (verify now)
- [ ] test_missing_field_blocks
- [ ] test_pass_marks_executable
- [ ] test_value_threshold_requires_human
- [ ] test_verdict_deterministic (100× loop)
- [ ] test_llm_never_decides_verdict
- [ ] test_pii_redacted_before_classify
- [ ] Full suite green
- [ ] Manual UI verdict colors

## Output format (mandatory)
```markdown
## Gate: F5
**Verdict:** PASS | FAIL
### Checklist
- [x] item — evidence
### Next dispatch for Cursor
One paragraph.
```

## Anti-scope reminder
No ontology, no engine rewrite, no F6 until Gate F5 PASS.
