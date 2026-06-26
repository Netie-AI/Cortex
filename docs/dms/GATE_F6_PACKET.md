# Gate F6 — Skill Capture (Consented, Opt-In)
**Date:** 2026-06-26 | **Branch:** dms-v2 | **Verdict:** PASS (Claude supervisor 2026-06-26)

## Scope
Record successful gate-passed task chains as internal reusable skill cards. Opt-in only (`DMS_SKILL_CAPTURE_ENABLED`). Skills boost F4 suggest ranking. Steward admin view for prune/deactivate.

## Test evidence
```
pytest tests/dms/test_skill_capture.py -q  → 4 passed
pytest tests/ -q                           → 145 passed, 4 skipped
```

## verify_all.ps1
Run locally (API/UI live checks skip if demo not running):
```
.\scripts\verify_all.ps1
```
Expected: import smoke + pytest + governance (capture default OFF) green.

## Gate F6 checklist
- [ ] test_capture_only_on_success_and_consent — gate pass + success → skill + ledger skill.captured; fail/blocked → noop
- [ ] test_capture_disabled_is_noop — flag OFF → no skills created
- [ ] test_captured_skill_boosts_suggestion — matching trigger_text boosts task confidence + skill_match
- [ ] test_deactivate_skill_excluded — deactivated skill excluded from suggest influence
- [ ] pytest tests/ -q → 145 passed, 4 skipped
- [ ] verify_all.ps1 green (pytest section)
- [ ] Manual: `/skills` page shows recording banner + skill list; deactivate works

## Files shipped
- `packs/dms/skills/capture.py`, `embed.py`, `__init__.py`
- `packs/dms/sql/006_skills_v0.sql`
- `packs/dms/tasks/suggest.py` (skill_match boost)
- `CortexOS/api/skill_routes.py`, `brain_routes.py` (outcome capture hook)
- `demo/dms-ui/app/skills/page.jsx`, `Sidebar.jsx`, `lib/api.js`
- `scripts/verify_all.ps1` (ASCII-safe + F6 checks)

## Governance invariants
- Capture only when `DMS_SKILL_CAPTURE_ENABLED=1` AND `gate_status=pass` AND `outcome=success`
- Never capture on fail/warn/blocked chains
- Skills internal-only; no export path

## Next after PASS
Phase 0 deploy planning (parallel OK). Review F7 security hardening status on branch.
