# CONTEXT.md — Netie / CortexOS / DMS Brain
**Keep under ~800 tokens. Locked decisions only — workflow in CURSOR_HANDOFF.md.**

---

## What this is
Netie forward-deployed AI platform. Focus: **DMS Brain** — governed warehouse/logistics for SMEs. Substrate: **CortexOS**. Vertical: `packs/dms/`. Sovereign: data on-box.

## Locked decisions
- **Packs:** `PACK=dms`. No vertical logic in CortexOS core.
- **Tiers:** T0/T1/T2 local hot loops. T3 = `BIG_API_PLACEHOLDER` cold paths only.
- **Compliance:** LLM extracts; rules decide. Deterministic only.
- **F1 ledger:** hash-chained append-only. SQLite demo; Postgres via `DMS_LEDGER_DSN`.
- **F7 PII:** `redact_for_prompt` before any model input. Non-negotiable.
- **Vision:** suggest + confirm; never auto-commit measurements.

## Gates
- V0 PASSED 2026-06-25
- V1, F1-hardened, F7, F2 PASS 2026-06-26

## Tests
`pytest -q` → 86 passed, 4 skipped

## Handoff files
| Audience | File |
|---|---|
| Claude supervisor | `CLAUDE_HANDOFF.md` |
| Cursor builder | `CURSOR_HANDOFF.md` |
| Regenerate | `python scripts/handoff.py --write` |

## Placeholders (never hardcode)
`BIG_API_PLACEHOLDER`, `EMBEDDER_MODEL`, `VISION_MODEL`, `DEPTH_SOURCE`, `WHATSAPP_BSP`, `COMPLIANCE_RULE_VERSION`

## Not in scope now
Palantir ontology, production WASM, respond.io — see `PARKING_LOT.md`.
