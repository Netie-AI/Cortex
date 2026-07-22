# Research findings index (P0_INDEX)

**Updated:** 2026-07-22 · Orchestrator-maintained  
**Rule:** research agents write markdown only under this folder. No Python/JSX.

## Active packet findings (A1–A5) — LANDED

| ID | File | Status | Verdict (one line) | Consumed by |
|---|---|---|---|---|
| A1 | [S1_DBOS_RESUME.md](S1_DBOS_RESUME.md) | **LANDED** | DBOS Transact `dbos==2.28.0`; Temporal docs-only | B1 |
| A2 | [S2_BROKER_SHORTLIST.md](S2_BROKER_SHORTLIST.md) | **LANDED** | NATS JetStream V1; Redpanda V2 | S2 later |
| A3 | [B1_STRESS_SUITE.md](B1_STRESS_SUITE.md) | **LANDED** | k6 + chaos + soak + knee design | B6 |
| A4 | [S1_TOKEN_BUDGET.md](S1_TOKEN_BUDGET.md) | **LANDED** | Detect=0 LLM; draft≤1 Q2; approve-gated publish | S1 meters |
| A5 | [P0_SECURITY_GAPS.md](P0_SECURITY_GAPS.md) | **LANDED** | Vault/NER unwired; RLS+SOPS open | Claude Code + B3 |

## Master maps

| Doc | Role |
|---|---|
| [TRUTH_GROUND_MAP.md](../../dms/TRUTH_GROUND_MAP.md) | Feature→file→test→state + cross-app links |
| [CURSOR_EXEC_PACKET_2026-07-22.md](../../dms/CURSOR_EXEC_PACKET_2026-07-22.md) | Cursor B* implementation order |
| [CLAUDE_CODE_SECURITY_PACKET.md](../../dms/packets/CLAUDE_CODE_SECURITY_PACKET.md) | High-end security for Claude Code |
| [P0_NER_TOKENVAULT.md](../../security/P0_NER_TOKENVAULT.md) | Security-track ontology |

## Who runs next

1. **Claude Code** → `CLAUDE_CODE_SECURITY_PACKET.md` (C-SEC-1…8), informed by A5  
2. **Cursor** → B3 F7 remainder (after Claude RLS/SOPS designs) → B4 F8 → B1 DBOS (A1) → …
