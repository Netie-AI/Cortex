# Gate F5 — Compliance Gate on Tasks
**Date:** 2026-06-26 | **Branch:** dms-v2 | **Verdict:** **PASS** (supervisor 2026-07-03)

## Scope
Deterministic compliance gate on suggested tasks before execution. PII-before-classify fix (Gate F4 conditional).

## Test evidence
```
pytest tests/dms/test_gate.py -q  → 6 passed (all checklist tests by name)
pytest tests/dms/test_f3_classify.py -q → 8 passed (incl. PII case)
pytest -q → 141 passed, 4 skipped (local re-run 2026-07-03)
```
Supervisor sandbox: 133 passed, 5 skipped — gap = sentence-transformers deps excluded; 0 failures.

## Gate F5 checklist
- [x] test_missing_field_blocks — missing quote_total_myr → fail, not executable
- [x] test_pass_marks_executable — complete template → pass + ledger task.gate_passed
- [x] test_value_threshold_requires_human — value_myr=10000 no ack → warn; with ack → pass
- [x] test_verdict_deterministic — same input 100× loop → identical status every run
- [x] test_llm_never_decides_verdict — mocked extractor random output; verdict unaffected
- [x] test_pii_redacted_before_classify — NRIC not in text reaching intent matcher
- [x] pytest -q → 141 passed, 4 skipped (local); supervisor 133/141 with ML-dep files excluded
- [~] verify_all.ps1 green — Windows-only; no contrary evidence
- [~] Manual UI verdict — demo run reported green; not independently observed by supervisor

## Files shipped
- `packs/dms/compliance/dms_rules_v1.yaml`
- `packs/dms/tasks/gate.py`, `extract.py`
- `packs/dms/sql/005_task_events_v0.sql`
- `CortexOS/api/task_routes.py`
- `packs/dms/classify/intent.py` (PII-before-classify)
- `demo/dms-ui/lib/api.js` (restored)
- `demo/dms-ui/app/chat/page.jsx`, `brain/page.jsx` (verdict UI)

## Next after PASS
**F6** — skill capture (consented, opt-in). See `docs/dms/BUILD_PLAN.md` § FEATURE 6.
