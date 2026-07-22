# STATUS.md
**Last updated:** 2026-07-22 | **Gate:** F6 **PASS** | **Active:** B3+F8+S1-durable landed → verify CI RLS green
**Rule:** Update after every gate. Read `CURSOR_HANDOFF.md` first.

> **2026-07-22 (late):** Cursor B3 (RLS CI + secrets + reversible wire + RBAC + `__future__` sweep),
> F8 `export_pptx` vertical slice, S1 durable resume (ops-DB checkpoints + optional DBOS).
> Cross-verify: [handback audit](docs/dms/packets/CLAUDE_CODE_SECURITY_HANDBACK_2026-07-22.md) + security review;
> fixed unguarded `depth_map` on estimate-dims.
> C-SEC-1..8 on `main` @ `6b407dd`. F7 remainder **PARTIAL→near-PASS** pending green RLS CI job.

---

## Current state at a glance

| Layer | Status | Gate |
|---|---|---|
| V0–V1, F1–F6 | Shipped | PASS |
| F7 remainder | RLS CI + secrets + RBAC expand | **Near-PASS** (CI must be green) |
| F8 tool-call (`export_pptx`) | Vertical slice shipped | Demo-safe |
| S1 agents + durable resume | Ops-DB checkpoints + optional DBOS | Core+B1 |
| Q1/Q2/L0–L2/S0 | Shipped | BUILD_PLAN_V2 |

## Test baseline
```
pytest -q  (expect ≥260; RLS skips without DSN)
python -m scripts.secrets_scan  → 0 findings
```

## Next three moves
1. Confirm GitHub Actions `RLS Proof` job green on push
2. `@agent` chat dispatch (last S1 skip)
3. U0 Data Studio / B6 stress suite code

## Handoff
- Truth map: `docs/dms/TRUTH_GROUND_MAP.md`
- Hand-back: `docs/dms/packets/CLAUDE_CODE_SECURITY_HANDBACK_2026-07-22.md`
- Research: `docs/research/findings/P0_INDEX.md`
