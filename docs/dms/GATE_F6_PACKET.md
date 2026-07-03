# Gate F6 — Skill Capture (Consented Learning Loop)
**Date:** 2026-07-03 | **Branch:** dms-v2 | **Verdict:** pending Claude review

## Scope
Opt-in capture of successful gated task chains as reusable internal skills. Feeds F4 `suggest()` via skill-match boost. Default OFF (`DMS_SKILL_CAPTURE_ENABLED`).

## Test evidence
```
pytest tests/dms/test_skill_capture.py -q → 4 passed
pytest -q → 145 passed, 4 skipped
```

## Gate F6 checklist
- [ ] test_capture_only_on_success_and_consent — gate fail / abandoned → no skill; success → skill + ledger
- [ ] test_capture_disabled_is_noop — flag OFF → no skill on success
- [ ] test_captured_skill_boosts_suggestion — matching trigger boosts confidence + skill_match
- [ ] test_deactivate_skill_excluded — deactivated skill no longer boosts
- [ ] Capture default OFF via env; admin toggle via API/UI
- [ ] Ledger `skill.captured` on capture; `skill.deactivated` on prune
- [ ] pytest -q → 145 passed, 4 skipped
- [ ] Manual: `/skills` page lists skills; steward can enable capture + deactivate

## Files shipped
- `packs/dms/skills/capture.py`, `__init__.py`
- `packs/dms/sql/006_dms_skills_v0.sql`
- `packs/dms/tasks/suggest.py` — `trigger_text` + skill boost
- `CortexOS/api/skill_routes.py` — list, config, complete, deactivate
- `CortexOS/api/brain_routes.py` — suggest accepts `trigger_text`
- `CortexOS/api/app.py` — register skill routes
- `demo/dms-ui/app/skills/page.jsx`, Sidebar, `lib/api.js`
- `tests/dms/test_skill_capture.py`

## Next after PASS
**F7 remainder** — RBAC, Postgres RLS, rate limiting (BUILD_PLAN § FEATURE 7 remainder).
