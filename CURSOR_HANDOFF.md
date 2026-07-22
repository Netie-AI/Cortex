<!-- generated 2026-07-22T05:44:12+00:00 -->
<!-- generated 2026-07-22T03:57:27+00:00 -->
<!-- generated 2026-07-03T05:16:44+00:00 -->
# CURSOR_HANDOFF — Builder Session Startup
**Read this file first.** Then `STATUS.md` → `docs/dms/BUILD_PLAN.md` § FEATURE 7 remainder.

---

## Your role
Builder. **F7 remainder in progress** — F6 PASS (2026-07-03). F8 blocked.

## Current state
| Item | Status |
|---|---|
| F6 skill capture | PASS |
| F7 remainder | RBAC on `/dms/skills/*` + rate limit shipped; RLS/SOPS next |
| Tests | **153 passed, 4 skipped** |

## F7 remainder — done this slice
- `packs/dms/security/api_auth.py` — API-key → viewer/steward/admin
- `packs/dms/security/rate_limit.py` — token bucket on `/dms/*`
- Skill routes use `Depends(require_role(...))`; actor from key, not body
- Demo UI sends `X-API-Key` per role switcher

## F7 remainder — still to ship
- RBAC on task/brain/audit mutators
- Postgres RLS proof test (`DMS_LEDGER_DSN` / CI)
- SOPS + secrets hygiene
- Gate packet when complete

## Build loop
1. Smallest diff per slice → `pytest -q` → CHANGELOG → STATUS → `handoff.py --write` → **git push**

## Demo
```powershell
.\demo\run_demo.ps1 -Fast
```
Skills: http://localhost:3000/skills (switch to STEWARD to toggle capture)
