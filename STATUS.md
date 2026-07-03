# STATUS.md
**Last updated:** 2026-07-03 | **Gate:** F6 **PASS** | **Active:** F7 remainder (RBAC slice shipped)
**Rule:** Update after every gate. Read `CURSOR_HANDOFF.md` first.

---

## Current state at a glance

| Layer | Status | Gate |
|---|---|---|
| V0 warehouse + V1 dimensioning | Shipped | PASS |
| F1 ledger (SQLite + Postgres DSN) | Shipped | PASS |
| F7 security + prompt harness | Shipped | PASS |
| F2 chat + F3 classify + persona | Shipped | PASS |
| F4 task suggest + Ponytail + Brain | Shipped | PASS |
| F5 compliance gate on tasks | Shipped | PASS |
| F6 skill capture | Shipped | **PASS** |
| **F7 remainder** | **In progress** | RBAC + rate limit slice shipped; RLS/SOPS pending |
| F8 tool-call execution | Packet on rail | After F7 remainder PASS |
| Demo (`run_demo.ps1 -Fast`) | **Live-ready** | Verified 2026-07-03 |
| CI | Green on push | |

## Test baseline
```
pytest -q → 153 passed, 4 skipped
```

## Active feature
**F7 remainder** — extend RBAC to more routes; Postgres RLS CI; SOPS. See `docs/dms/BUILD_PLAN.md` § FEATURE 7.

## Next three moves
1. Extend API-key RBAC beyond `/dms/skills/*` (tasks, brain mutators, audit filter)
2. `test_rls_blocks_out_of_scope_read` with Postgres CI DSN
3. Gate F7 remainder → then F8 tool-call execution

## Handoff
- **Claude:** `CLAUDE_HANDOFF.md`
- **Cursor:** `CURSOR_HANDOFF.md`
- **Specs:** `docs/dms/BUILD_PLAN.md` § F7, `docs/dms/GATE_F8_PACKET.md`

## Design constraints
- API `actor` from authenticated key — never trust client-supplied role/actor on mutating routes
- Demo keys: `dms-demo-{viewer,steward,admin}-key` (rotate in prod via `DMS_API_KEYS`)
- F8 blocked until F7 remainder PASS
