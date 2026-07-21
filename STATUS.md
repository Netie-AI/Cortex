# STATUS.md
**Last updated:** 2026-07-20 | **Gate:** F6 **PASS** | **Active:** BUILD_PLAN_V2 wave 1 (L0 next) + F7 remainder
**Rule:** Update after every gate. Read `CURSOR_HANDOFF.md` first.

> **2026-07-20:** `docs/dms/BUILD_PLAN_V2_LAKEHOUSE.md` is the active master plan
> (lakehouse + 99% answer engine + streaming agents + Data Studio). Accuracy benchmark
> baseline: core 100%, safety 100%, target 0/14 (`python -m bench.accuracy`). Parking-lot
> P3/P6/P12 activated, P1 partial.
> **L0 lakehouse LANDED** (DuckLake, `python -m scripts.lakehouse_migrate`, `/dms/lakehouse/*`).
> **Q1 governed semantic layer LANDED** (`packs/dms/semantic/`: 28 metrics, 18 certified,
> value dicts + guardrail-verified compiler).
> **Q2 adaptive answer engine LANDED** (`CortexOS/dms/answer_engine.py`): certified →
> governed metric → (L2 flag-off) → abstain. **Accuracy gate MET: core 18/18, safety 4/4,
> target 14/14 — 100% answered-precision, zero confident-wrong** (`python -m bench.accuracy`).
> **L1 + L2 LANDED (P6 SHIPPED)** — `packs/dms/ingest/` + `packs/dms/pipelines/`.
> **S0 streaming intake LANDED** (`packs/dms/streams/`, `/dms/streams/*`): webhook → bronze,
> dedup, backpressure, simulator. **Stress-tuned: streaming ingest 25→419 events/s** (fixed a
> DuckLake `executemany` per-row-commit pathology via `tables.bulk_insert`). Test baseline now
> **217 passed, 4 skipped**; accuracy 100% all tiers. Next: **S1 watcher agents** (detect →
> draft → compliance gate → human approve) or U0 Data Studio; F8 tool-call for governed publish.

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
