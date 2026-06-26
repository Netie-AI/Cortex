# STATUS.md
**Last updated:** 2026-06-26 | **Gate:** F6 PASS → **Phase 0 deploy planning**
**Rule:** Update after every gate. New Claude/Cursor sessions read this first.

---

## Current state at a glance

| Layer | Status | Gate |
|---|---|---|
| F1–F6, Ponytail, Brain | Shipped | PASS |
| F7 core (PII/crypto/RLS SQL) | Shipped | F7 PASS — SOPS + rate limit debt |
| **Phase 0 deploy** | **Planning** | Gate P0 pending |
| V2/V3 vision | Planned | After pilot |

## Test baseline
```
pytest tests/ -q → 145 passed, 4 skipped
```

## Active work
**Phase 0 deploy planning** — see `docs/dms/PHASE0_PLAN.md`  
**Doc sync:** `ARCHITECTURE.md` updated (F3–F6 Shipped | F6 PASS)

## Next three moves
1. Supervisor approves Phase 0 plan scope
2. Ship P0.1 — `demo/env.example` + run_demo self-bootstrap
3. F7 remainder (SOPS, rate limit) — parallel pre-pilot track

## Handoff
- **Claude:** `CLAUDE_HANDOFF.md`
- **Spec:** `docs/dms/PHASE0_PLAN.md`
