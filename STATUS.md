# STATUS.md
**Last updated:** 2026-07-22 | **Gate:** F6 **PASS** | **Active:** Claude Code C-SEC-1..8 LANDED → Cursor B3 (RLS CI + secrets hook)
**Rule:** Update after every gate. Read `CURSOR_HANDOFF.md` first.

> **2026-07-22 (PM):** Parallel research A1–A5 landed under `docs/research/findings/`.
> Truth map: `docs/dms/TRUTH_GROUND_MAP.md`. Claude high-end security:
> `docs/dms/packets/CLAUDE_CODE_SECURITY_PACKET.md`. `main` includes wave1+S1 @ `79ed093`.
>
> **2026-07-22 (AM):** Wave1 on `dms-integrated-engine`. S1 smoke + stream ~379 ev/s.
> S1 remainder: DBOS (A1 verdict), `@agent` chat, F8 publish.

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
1. **Claude Code:** run `docs/dms/packets/CLAUDE_CODE_SECURITY_PACKET.md` (C-SEC-1…4 first)
2. **Cursor:** B3 F7 remainder using Claude hand-backs → then B4 F8
3. **Cursor:** B1 DBOS resume per `docs/research/findings/S1_DBOS_RESUME.md`

## Handoff
- **Claude Code (security):** `docs/dms/packets/CLAUDE_CODE_SECURITY_PACKET.md`
- **Claude supervisor:** `CLAUDE_HANDOFF.md`
- **Cursor builder:** `CURSOR_HANDOFF.md` + `docs/dms/CURSOR_EXEC_PACKET_2026-07-22.md`
- **Truth map:** `docs/dms/TRUTH_GROUND_MAP.md`
- **Research index:** `docs/research/findings/P0_INDEX.md`

## Design constraints
- API `actor` from authenticated key — never trust client-supplied role/actor on mutating routes
- Demo keys: `dms-demo-{viewer,steward,admin}-key` (rotate in prod via `DMS_API_KEYS`)
- F8 blocked until F7 remainder PASS
