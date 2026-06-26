# Gate F5 — Compliance Gate on Tasks
**Date:** 2026-06-26 | **Branch:** dms-v2 | **Verdict:** pending Claude review

## Scope
Deterministic compliance gate on suggested tasks before execution. PII-before-classify fix (Gate F4 conditional).

## Test evidence
```
pytest tests/dms/test_gate.py -q  → 6 passed
pytest tests/dms/test_f3_classify.py -q → 8 passed (incl. PII case)
pytest -q → 141 passed, 4 skipped
```

## Gate F5 checklist
- [ ] test_missing_field_blocks — missing quote_total_myr → fail, not executable
- [ ] test_pass_marks_executable — complete template → pass + ledger task.gate_passed
- [ ] test_value_threshold_requires_human — value_myr=10000 no ack → warn; with ack → pass
- [ ] test_verdict_deterministic — same input 100× loop → identical status every run
- [ ] test_llm_never_decides_verdict — mocked extractor random output; verdict unaffected
- [ ] test_pii_redacted_before_classify — NRIC not in text reaching intent matcher
- [ ] pytest -q → 141 passed, same 4 skipped
- [ ] verify_all.ps1 green
- [ ] Manual: inbound message → task select → green/amber/red verdict in UI

## Files shipped
- `packs/dms/compliance/dms_rules_v1.yaml`
- `packs/dms/tasks/gate.py`, `extract.py`
- `packs/dms/sql/005_task_events_v0.sql`
- `CortexOS/api/task_routes.py`
- `packs/dms/classify/intent.py` (PII-before-classify)
- `demo/dms-ui/lib/api.js` (restored)
- `demo/dms-ui/app/chat/page.jsx`, `brain/page.jsx` (verdict UI)

## Next after PASS
**F6** — skill capture (consented, opt-in). Do NOT start until Gate F5 PASS.
