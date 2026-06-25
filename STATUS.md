# STATUS.md
**Last updated:** 2026-06-26 | **Gate:** V1, F1, F7, F2 **PASS** (see docs/dms/GATE_RESULTS_2026-06-26.md)
**Rule:** Update after every gate. New sessions read `CURSOR_HANDOFF.md` or `CLAUDE_HANDOFF.md`.

---

## Current state at a glance

| Layer | Status | Gate |
|---|---|---|
| CortexOS runtime (DAG, pack loader, compliance substrate) | Stable | — |
| DMS pack (PACK=dms loads clean) | Stable | — |
| Demo script (`run_demo.ps1`) | Fixed | Verified |
| F1 audit ledger (SQLite + optional Postgres DSN) | **Shipped** | Gate F1-hardened pending |
| F7 EXIF strip (photo sanitize) | Shipped | V0 PASS |
| F7 full security (PII, AES-GCM, RLS SQL) | **Shipped (minimal)** | Gate F7 pending |
| V0 warehouse spine | Shipped | V0 PASS |
| V1 dimensioning | Shipped | **Gate V1 pending** |
| F2 governed chat foundation | **Shipped** | After Gate V1 |
| F3 classify | Planned | After F2 verify |
| Phase 0 deploy (docker-compose, Caddy) | Not started | Parallel OK |

## Test baseline
```
pytest -q → 86 passed, 4 skipped
Skipped: postgres ledger (no DMS_LEDGER_DSN), apscheduler
```

## Handoff protocol
- **Claude:** `CLAUDE_HANDOFF.md` or `python scripts/handoff.py --claude --write`
- **Cursor:** `CURSOR_HANDOFF.md` or `python scripts/handoff.py --cursor`
- **After ship/gate:** `python scripts/handoff.py --write`

## Active feature
**F3 classify** — intent + sentiment on inbound chat messages (local T0/T1). Parallel: Phase 0 deploy planning.

## Next three moves
1. Claude supervisor PASS on gate packet (V1 + F1 + F7)
2. Ship F3 — intent + sentiment classify (local T0/T1)
3. Phase 0 — docker-compose + Caddy for dms.netie.ai

## Known debt
| Debt | Fix | Milestone |
|---|---|---|
| Postgres ledger CI | Set DMS_LEDGER_DSN in CI or document manual gate | Before prod |
| SOPS + rate limiting | Phase 3 remainder | Before real customer |
| Ontology (P1) | After 1+ paying client | H2 |

## Design constraints (do not violate)
- No PII in LLM prompts without `redact_for_prompt`
- No BIG_API in hot loops
- All writes → F1 ledger
- Sequential dependent features; parallel explore only
