# STATUS.md
**Last updated:** 2026-07-22 | **Gate:** F7 remainder **PASS** (RLS CI green) | **Active:** `@agent` chat + U0 Studio
**Rule:** Update after every gate. Read `CURSOR_HANDOFF.md` first.

> **2026-07-22 (CI green):** Fixed Poetry `postgres` extra (`psycopg[binary]` invalid in
> `tool.poetry.extras`), added `pytz` for DuckLake, opaque `ghs_` scanner (May 2026 token
> changelog), and RLS proof as NOSUPERUSER `dms_rls_app` (superuser bypasses FORCE RLS).
> `Test` + `Secrets Scan` + `RLS Proof` green on `main` / `dms-integrated-engine`.

---

## Current state at a glance

| Layer | Status | Gate |
|---|---|---|
| V0–V1, F1–F6 | Shipped | PASS |
| F7 remainder | RLS CI + secrets + RBAC | **PASS** (CI green) |
| F8 tool-call (`export_pptx`) | Vertical slice shipped | Demo-safe |
| S1 agents + durable resume | Ops-DB checkpoints + optional DBOS | Core+B1 |
| Q1/Q2/L0–L2/S0 | Shipped | BUILD_PLAN_V2 |

## Test baseline
```
pytest -q  (expect ≥270; local RLS skips without DSN)
python -m scripts.secrets_scan  → 0 findings
CI: Test + Secrets Scan + RLS Proof → success
```

## Next three moves
1. `@agent` chat dispatch (last S1 skip)
2. U0 Data Studio / B6 stress suite code
3. World-engine brief for Claude Code (`docs/dms/packets/CLAUDE_CODE_WORLD_ENGINE_BRIEF_2026-07-22.md`)

## Handoff
- Truth map: `docs/dms/TRUTH_GROUND_MAP.md`
- Hand-back: `docs/dms/packets/CLAUDE_CODE_SECURITY_HANDBACK_2026-07-22.md`
- Research: `docs/research/findings/P0_INDEX.md`
