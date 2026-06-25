# STATUS.md
**Last updated:** 2026-06-26 | **Gate:** V1/F1/F7/F2/F3-security **PASS**
**Rule:** Update after every gate. Read `CURSOR_HANDOFF.md` first.

---

## Current state at a glance

| Layer | Status | Gate |
|---|---|---|
| F1 ledger (SQLite + Postgres DSN) | Shipped | PASS |
| F7 PII/crypto/RLS + prompt harness | Shipped | PASS |
| F2 chat + F3 classify | Shipped | PASS |
| Security adversarial corpus (15 cases) | Shipped | PASS |
| WASM fuel sandbox | Shipped | PASS |
| Persona routing (psychological state) | Shipped | — |
| Local GPU inference (Qwen opt-in) | Script ready | Manual |
| F4 task suggest | **Next** | After F3 verify |
| Phase 0 deploy | Planned | Parallel OK |
| Ontology (P1) | Parked | Condition not met |

## Test baseline
```
pytest -q → 103 passed, 4 skipped
```

## Active feature
**F4 task suggest + batch learning** — suggestion engine over classified message history.

## Next three moves
1. Ship F4 — task suggest from intent + history
2. Run `scripts/setup_gpu_env.ps1` + optional Qwen fine-tune on warehouse corpus
3. Phase 0 — docker-compose + Caddy for dms.netie.ai

## GPU setup (RTX 4070)
```powershell
.\scripts\setup_gpu_env.ps1
$env:CORTEX_LOCAL_INFERENCE = "1"
```

## Handoff
- Claude: `CLAUDE_HANDOFF.md` or `python scripts/handoff.py --claude --write`
- Cursor: `CURSOR_HANDOFF.md`
