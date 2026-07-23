# CONTEXT.md — Netie / CortexOS / DMS Brain
**Keep under ~800 tokens. Locked decisions only — workflow in CURSOR_HANDOFF.md.**

---

## What this is
Netie forward-deployed AI platform. Focus: **DMS Brain** — governed warehouse/logistics for SMEs. Substrate: **CortexOS**. Vertical: `packs/dms/`. Sovereign: data on-box.

## Repo shape
```
Cortex/
├── CortexOS/          runtime (API, DAG, compliance, ponytail middleware)
├── packs/dms/         vertical (vision, audit, chat, tasks, brain, security)
├── demo/dms-ui/       Next.js demo (query, warehouse, chat, brain)
├── docs/dms/          build plans, gate packets, DEMO.md
├── STATUS.md          build state (read first)
├── CLAUDE_HANDOFF.md  supervisor paste block
└── CURSOR_HANDOFF.md  builder startup
```

## Locked decisions
- **Packs:** `PACK=dms`. No vertical logic in CortexOS core.
- **Tiers:** T0/T1/T2 local hot loops. T3 = `BIG_API_PLACEHOLDER` cold paths only.
- **Compliance:** LLM extracts; rules decide pass/fail. Deterministic only (F5).
- **F1 ledger:** hash-chained append-only. SQLite demo; Postgres via `DMS_LEDGER_DSN`.
- **F7 PII:** `secure_for_prompt()` / `redact_for_prompt` before any model input.
- **Vision:** suggest + confirm; never auto-commit measurements.
- **Ponytail:** YAGNI token discipline — see `docs/PONYTAIL.md`.
- **Context engineering:** layered assemble + compaction + NOTES — see `docs/CONTEXT_ENGINEERING.md`.

## Gates
- V0 PASSED 2026-06-25
- V1, F1-hardened, F7 (PII harness), F2, F3-security, F4 PASS 2026-06-26
- **F5 PASS** 2026-07-03
- **F6 PASS** 2026-07-03 — skill capture
- **Active:** F7 remainder (RBAC/RLS/rate limit)

## Tests
`pytest -q` → **153 passed, 4 skipped**

## Demo (show now)
```powershell
.\demo\run_demo.ps1 -Fast
```
http://localhost:3000 · /warehouse · /chat · /brain

## Handoff files
| Audience | File |
|---|---|
| Claude supervisor | `CLAUDE_HANDOFF.md` |
| Cursor builder | `CURSOR_HANDOFF.md` |
| Regenerate both | `python scripts/handoff.py --write` |

## Placeholders (never hardcode)
`BIG_API_PLACEHOLDER`, `EMBEDDER_MODEL`, `VISION_MODEL`, `DEPTH_SOURCE`, `WHATSAPP_BSP`, `COMPLIANCE_RULE_VERSION`

## Not in scope now
Palantir ontology (P1), production WASM (P2), full respond.io (P9) — see `PARKING_LOT.md`.
