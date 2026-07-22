# STATUS.md
**Last updated:** 2026-07-22 | **Gate:** F6 **PASS** | **Active:** BUILD_PLAN_V2 wave 1 complete → S1 remainder + U0/F8
**Rule:** Update after every gate. Read `CURSOR_HANDOFF.md` first.

> **2026-07-22:** Wave1 (Q1/Q2/L1/L2/S0/S1-core) is on `dms-integrated-engine`.
> S1 smoke tests landed (`tests/dms/test_s1_agents.py`); stream stress re-verified ~379 ev/s,
> 0 errors. S1 remainder: DBOS durable resume, `@agent` chat dispatch, F8 publish tools.
> Next feature choices: **U0 Data Studio** or **F8 tool-call** (after F7 remainder RLS/SOPS).
>
> **2026-07-20:** `docs/dms/BUILD_PLAN_V2_LAKEHOUSE.md` is the active master plan
> (lakehouse + 99% answer engine + streaming agents + Data Studio).
> **L0/Q1/Q2/L1/L2/S0 LANDED.** S1 core (detectors/employee/registry/API) LANDED; DBOS slice open.
> Stream stress-tuned (25→~380–419 ev/s). Accuracy gate MET (core/safety/target 100%, zero wrong).

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
pytest -q → 237 passed, 6 skipped (S1 adds 2 skips for DBOS/@agent)
python -m bench.stress --scenario stream → ~380 ev/s, 0 errors
```

## Active feature
**S1 remainder + merge-to-main** — core watcher agents shipped; DBOS resume + chat dispatch open.
**F7 remainder** still open (RLS CI + SOPS) before full F8.

## Next three moves
1. Merge `dms-integrated-engine` → `main` (PR)
2. U0 Data Studio OR finish F7 remainder → F8 (governed publish for S1)
3. S1 DBOS durable resume + `@agent` chat dispatch (parallelizable after F8 substrate)

## Handoff
- **Claude:** `CLAUDE_HANDOFF.md`
- **Cursor:** `CURSOR_HANDOFF.md`
- **Specs:** `docs/dms/BUILD_PLAN_V2_LAKEHOUSE.md`, `docs/dms/GATE_F8_PACKET.md`
- **Cursor low-level packet:** see chat protocol block / `docs/dms/CURSOR_EXEC_PACKET_2026-07-22.md`

## Design constraints
- API `actor` from authenticated key — never trust client-supplied role/actor on mutating routes
- Demo keys: `dms-demo-{viewer,steward,admin}-key` (rotate in prod via `DMS_API_KEYS`)
- F8 blocked until F7 remainder PASS
