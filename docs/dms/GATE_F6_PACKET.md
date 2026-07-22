# Gate F6 — Skill Capture (Consented Learning Loop)
**Date:** 2026-07-03 | **Branch:** dms-v2 | **Verdict:** **PASS** (supervisor 2026-07-03)

## Scope
Opt-in capture of successful gated task chains as reusable internal skills. Feeds F4 `suggest()` via skill-match boost. Default OFF (`DMS_SKILL_CAPTURE_ENABLED`).

## Test evidence
```
pytest tests/dms/test_skill_capture.py -q → 4 passed (all checklist tests by name)
pytest -q → 145 passed, 4 skipped (local); supervisor 137/145 (ML-dep files excluded)
```

## Gate F6 checklist
- [x] test_capture_only_on_success_and_consent
- [x] test_capture_disabled_is_noop
- [x] test_captured_skill_boosts_suggestion
- [x] test_deactivate_skill_excluded
- [x] Ledger `skill.captured` / `skill.deactivated` — verified in capture.py
- [x] pytest -q green
- [~] Manual `/skills` UI — demo reported; not independently observed
- [x] Known gap named: client-supplied `actor` on skill routes — fixed in F7 remainder slice `a78c90e+`

## Next after PASS
**F7 remainder** — API-key RBAC, Postgres RLS, rate limiting.
