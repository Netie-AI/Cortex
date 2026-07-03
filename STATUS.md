# STATUS.md
**Last updated:** 2026-07-03 | **Gate:** F5 shipped → **Gate F5 pending (Claude)**
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
| **F5 compliance gate on tasks** | **Shipped** | **Gate F5 pending** |
| Demo (`run_demo.ps1 -Fast`) | **Live-ready** | Verified 2026-07-03 |
| CI (`.github/workflows/test.yml`) | Fixed | Green on push |
| F6 skill capture | Planned | After Gate F5 PASS |
| Phase 0 deploy (docker-compose) | Planned | Parallel-plan only |
| Ontology (P1) | Parked | Condition not met |

## Test baseline
```
pytest -q → 141 passed, 4 skipped
Skipped: postgres ledger (no DMS_LEDGER_DSN), apscheduler, optional deps
```

## Demo (show immediately)
```powershell
.\demo\run_demo.ps1 -Fast
```
| Page | URL |
|---|---|
| Query | http://localhost:3000 |
| Warehouse | http://localhost:3000/warehouse |
| Chat + F5 verdict | http://localhost:3000/chat |
| Brain generative | http://localhost:3000/brain |
| API health | http://localhost:8000/health |

Guide: `docs/DEMO.md`

## Active feature
**Gate F5** — Claude supervisor verifies compliance gate. Paste `CLAUDE_HANDOFF.md` + `docs/dms/GATE_F5_PACKET.md`.

## Next three moves
1. Claude PASS on Gate F5 → update this file + CONTEXT.md
2. Ship **F6** skill capture (consented, opt-in)
3. Phase 0 deploy planning (docker-compose + Caddy) — parallel OK

## Known debt
| Debt | Fix | When |
|---|---|---|
| Postgres ledger CI | Set `DMS_LEDGER_DSN` in CI | Before prod |
| SOPS + rate limiting | F7 remainder | Before real customer |
| Qwen fine-tune | `scripts/finetune_dms_tone.py` | Optional GPU |

## Handoff
- **Claude:** `CLAUDE_HANDOFF.md` or `python scripts/handoff.py --claude --write`
- **Cursor:** `CURSOR_HANDOFF.md`
- **Specs:** `docs/dms/F5_PLAN.md`, `docs/dms/GATE_F5_PACKET.md`

## Design constraints (do not violate)
- No PII in prompts without security harness
- No BIG_API in hot loops
- LLM extracts; rules decide (F5)
- All writes → F1 ledger
- F6 blocked until Gate F5 PASS
