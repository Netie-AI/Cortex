# CLAUDE_HANDOFF — Supervisor / Gate Session
**Auto-sync:** run `python scripts/handoff.py --write` after every ship or gate.
**Paste this entire file into a new Claude chat.**

---

## Your role
External supervisor. Verify gate packets; return PASS/FAIL. No code implementation.

## Current state
| Field | Value |
|---|---|
| F1–F6 + Ponytail + Brain | **All PASS** |
| F7 core | **PASS** (SOPS + rate limit = pre-pilot debt) |
| Active | **Phase 0 deploy planning** |

## Phase 0 scope (`docs/dms/PHASE0_PLAN.md`)
- `run_demo.ps1` green without manual env prep
- `verify_all.ps1` 19/19 on clean machine (demo running)
- Postgres DSN wired (`DMS_LEDGER_DSN`, ops migrations)
- docker-compose + Caddy (TLS)

## Test snapshot
`pytest tests/ -q` → **145 passed, 4 skipped**

## Architecture doc
`ARCHITECTURE.md` synced — F3–F6 marked Shipped | F6 PASS.

## Invariant (do not blur)
F6 skills feed suggest only. F5 YAML rules govern execution. P14 in PARKING_LOT if ever proposed.

## Next builder dispatch (after P0 plan approval)
Ship **P0.1** — `demo/env.example` + run_demo env bootstrap.
