# STATUS.md
**Last updated:** 2026-07-21 | **Gate:** F6 **PASS** | **Active:** integrated engine line + F7 remainder
**Rule:** Update after every gate. Read `CURSOR_HANDOFF.md` first.

---

## Current state at a glance

| Layer | Status | Gate |
|---|---|---|
| V0–V1, F1–F6, Ponytail, Brain | Shipped | PASS |
| F7 remainder (RBAC + rate limit on skills) | Shipped slice | F7 remainder in progress |
| Engine registry + memory plane + AirGPT sidecar | Ported from `netie-engine-up` | Live API |
| L0 DuckLake lakehouse | Ported | BUILD_PLAN_V2 |
| Phase 0 deploy plan | Documented | `docs/dms/PHASE0_PLAN.md` |
| Demo (`run_demo.ps1 -Fast`) | Live-ready | Verified on dms-v2 |

## Test baseline
```
pytest tests/ -q  (expect ≥153; engine/lakehouse tests added)
```

## Active feature
Integrated branch `dms-integrated-engine` = latest `dms-v2` + engine/memory/lakehouse from `netie-engine-up` + Phase 0 plan.

## Next three moves
1. Confirm pytest green after engine port
2. Run `.\demo\run_demo.ps1 -Fast` — UI: QUERY / CHAT / BRAIN / SKILLS
3. Extend F7 RBAC beyond skills; then F8 tool-call execution

## Handoff
- **Claude:** `CLAUDE_HANDOFF.md`
- **Cursor:** `CURSOR_HANDOFF.md`
- **Specs:** `docs/dms/BUILD_PLAN_V2_LAKEHOUSE.md`, `docs/dms/PHASE0_PLAN.md`
