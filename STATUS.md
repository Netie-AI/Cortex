# STATUS.md
**Last updated:** 2026-07-03 | **Gate:** F6 shipped → **Gate F6 pending (Claude)**
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
| **F6 skill capture** | **Shipped** | **Gate F6 pending** |
| Demo (`run_demo.ps1 -Fast`) | **Live-ready** | Verified 2026-07-03 |
| CI (`.github/workflows/test.yml`) | Fixed | Green on push |
| F7 remainder (RBAC/RLS/rate limit) | Planned | After Gate F6 PASS |
| F8 tool-call execution | Draft packet (local) | After F7 remainder |
| Phase 0 deploy (docker-compose) | Planned | Parallel-plan only |
| Ontology (P1) | Parked | Condition not met |

## Test baseline
```
pytest -q → 145 passed, 4 skipped
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
| Skills admin (F6) | http://localhost:3000/skills |
| API health | http://localhost:8000/health |

Guide: `docs/DEMO.md`

## Active feature
**Gate F6** — Claude supervisor verifies skill capture. Paste `CLAUDE_HANDOFF.md` + `docs/dms/GATE_F6_PACKET.md`.

## Next three moves
1. Claude PASS on Gate F6 → update this file + CONTEXT.md
2. Ship **F7 remainder** (RBAC, Postgres RLS, rate limiting)
3. F8 tool-call execution (commit `GATE_F8_PACKET.md` when F8 opens on rail)

## Known debt
| Debt | Fix | When |
|---|---|---|
| Postgres ledger CI | Set `DMS_LEDGER_DSN` in CI | Before prod |
| SOPS + rate limiting | F7 remainder | Before real customer |
| `GATE_F8_PACKET.md` | Commit when F8 enters BUILD_PLAN | After F7 remainder |
| Qwen fine-tune | `scripts/finetune_dms_tone.py` | Optional GPU |

## Handoff
- **Claude:** `CLAUDE_HANDOFF.md` or `python scripts/handoff.py --claude --write`
- **Cursor:** `CURSOR_HANDOFF.md`
- **Specs:** `docs/dms/GATE_F6_PACKET.md`, `docs/dms/BUILD_PLAN.md` § F7

## Design constraints (do not violate)
- No PII in prompts without security harness
- No BIG_API in hot loops
- LLM extracts; rules decide (F5)
- All writes → F1 ledger
- F6 capture default OFF; no covert capture; no off-box export
- Do not start F7 remainder or F8 until Gate F6 PASS
