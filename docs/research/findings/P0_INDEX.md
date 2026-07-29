# Research findings index (P0_INDEX)

**Updated:** 2026-07-26 · Orchestrator-maintained  
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

## Generative orchestration (G1) — MERGED · runtime P0/P1 shipped

| ID | File | Status | Verdict (one line) | Consumed by |
|---|---|---|---|---|
| G1 | [G1_GEN_CFSM_JEPA.md](G1_GEN_CFSM_JEPA.md) | **MERGED** + **P0/P1 code** | Finite-horizon gen-DAG + JEPA collapse + vault habits; static DAG still wins known SOPs | `execution/gen_cfsm.py` |
| G1b | [G1_STRESS_AGENT_BAKEOFF.md](G1_STRESS_AGENT_BAKEOFF.md) | **LANDED** | EST. composites DAG 4.1 > gen-cFSM 4.0 ≫ ReAct/OpenClaw; S1–S7 + claim gates | B6-style harness later |
| G2 | [ENTERPRISE_GEN_CFSM_LOOP_PLAN.md](../../strategy/ENTERPRISE_GEN_CFSM_LOOP_PLAN.md) | **PLAN** | **Proactive-first** goal-seeking + ethical enterprise bind; reactive open-set secondary; JEPA + telemetry + update port | PARKING_LOT **P21** |

## Who runs next

1. **Claude — G2.3 OSR:** `docs/dms/packets/CURSOR_TO_CLAUDE_G2_3_OSR_2026-07-27.md`  
2. **Cursor — optional CDP / app deep-link** — `docs/dms/packets/NEXT_LANES.md`  
3. **Then** G2.4 telemetry (held until G2.3 hands back)
