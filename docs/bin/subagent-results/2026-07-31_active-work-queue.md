# Active Work Queue — DMS + Netie Engine (2026-07-31)

**Orientation:** Spaces sandbox product on engine-first north star.  
**Sources:** `STATUS.md`, `PARKING_LOT.md` (P12/P17/P18/P21/P22), `DMS_SPACES_PRODUCT_2026-07-29.md`, `CORTEX_FINAL_GOAL.md`, `NEXT_LANES.md`, `CLAUDE_CODE_HANDOFF_NEXT.md`, `DMS_EVAL_AND_STRESS_PLAN.md`.

**Rule:** Max 12 active tracks. Everything else is parked or binned.

---

## Active Work Queue

| ID | Name | Why it matters | Status | Canonical doc | Anti-scope |
|----|------|----------------|--------|---------------|------------|
| **W1** | **DMS Phase 0 — Postgres ops + RLS** | Spaces, amend, concurrent stewards, and honest Runs/Admin all need a real ops plane with row-level security. Without this, every mutation path is demo theatre. | **NOW** | `D:\Cortex\docs\strategy\DMS_SPACES_PRODUCT_2026-07-29.md` | No Excel write-back; no MinIO; no CortexOS import from DMS; no "thousands of connectors" |
| **W2** | **Amend Proposal loop (Phase 1)** | Product differentiator vs cloud lakes: versioned Proposal → confirm token → apply → hash receipt → rollback. Binding build order step after Phase 0. | **NOW** | `D:\Cortex\docs\strategy\DMS_SPACES_PRODUCT_2026-07-29.md` | No bidirectional Excel sync; no apply without confirm token; no weakening `compliance_gate` |
| **W3** | **C7 import boundary (I1)** | `answer_engine → packs.dms.generative` breaks C2 and `lint-imports` — CI cannot honestly gate merges until fixed with Protocol extraction or narrow move behind `query_service`. | **NOW** | `D:\Cortex\docs\dms\packets\CLAUDE_CODE_HANDOFF_NEXT.md` | No silent `.importlinter` edits; no `_C2_ALLOWLIST` expansion; no `INVARIANT-CHANGE:`-less protected-path edits |
| **W4** | **Eval corpus Phase 1b → N=310** | "0 confidently wrong" is statistically honest only at ~300+ cases. Seeds (47) exist; expansion must use `gold_verified` gate so Trust claim stays false until real. | **NEXT** | `D:\Cortex\docs\dms\DMS_EVAL_AND_STRESS_PLAN.md` | No `claim.supported: true` early; no CRAG/BIRD in same slice; no hostile-SQL reclassification |
| **W5** | **Spaces MVP — persist + sandbox enforce** | Core product bet: named sandboxes over selected sources; data-plane intersection, not UI hide. In-memory create is a stub until Postgres ACL lands. | **NEXT** | `D:\Cortex\docs\strategy\DMS_SPACES_PRODUCT_2026-07-29.md` | No Pointer/Clicks in DMS demo; no company-wide leak around Space boundary; no break-glass without ledger |
| **W6** | **Trust + Ontology honesty stack** | Engine read APIs + DMS Trust UI must show N/310, blockers, and `supported: false` until evidence is real. `:8010` must load routes; eval `passed` must mean `True`, not "not False". | **NOW** | `D:\Cortex\docs\dms\packets\CLAUDE_CODE_HANDOFF_NEXT.md` | No green Trust chip while blockers exist; no claiming Ontology live on `:8010` without restart verify |
| **W7** | **C7 schema gate hardening (product Phase 2)** | Breaks ~65% paraphrase ceiling *safely*: schema retrieval → sqlglot → EXPLAIN → bounded retry → abstain. Engine capability that Spaces Q&A depends on. | **NEXT** | `D:\Cortex\docs\dms\packets\CORTEX_TO_DMS_C7_KICKOFF.md` | No manifest refusal weakening; no raw-SQL leaderboard tuning without envelope assertions |
| **W8** | **BIRD eval adapter (Phase 3)** | Measures the actual job (messy DBs, execution path, abstain bucket) — higher product signal than CRAG. Fills the gap C7-full created with no benchmark. | **NEXT** | `D:\Cortex\docs\dms\DMS_EVAL_AND_STRESS_PLAN.md` | No collapsing abstain into incorrect; no optimizing execution accuracy without `assert_envelope_valid` |
| **W9** | **P22 C-line mins: C5 → C8** | C5 (tool classes / refuse agent→apply) and C8 (durable `query_run`) unblock governed mutations and C10 plausibility. Ordered queue after T7-min shipped. | **NEXT** | `D:\Cortex\PARKING_LOT.md` (§ P22) | No C11 alias graph before C5/C8; no multi-pool memory broker yet |
| **W10** | **OpenVault P17a → G2.6 update port** | Self-host engine needs offline verify, anti-rollback `update_generation`, ship-gate parity. G2.6 blocked until vault hands back `trust_root` + `verify_bundle`. | **BLOCKED** | `D:\Cortex\docs\dms\packets\CLAUDE_TO_OPENVAULT_P17A_2026-07-27.md` | No auto-apply updates; no client secret on laptop; no network in verify path |
| **W11** | **Spaces hard scenarios (3GB sandbox)** | Proves the wedge: ~3GB mixed Excels, personal+team ACL, correlate SKU, no leak outside Space. Required before demo claims. | **NEXT** | `D:\Cortex\docs\strategy\DMS_SPACES_PRODUCT_2026-07-29.md` | No 100TB analytics design; no petabyte ETL; no pure-vector part-number retrieval without BM25 |
| **W12** | **P17 engine product surface** | North star: hosted API + downloadable netie engine with same governed core. O4 SDK exists; wire packaging and external consumption paths. | **NEXT** | `D:\Cortex\docs\strategy\CORTEX_FINAL_GOAL.md` | No vertical features in CortexOS; no DMS-specific UI in engine; no bypass around `call_action` / ledger |

---

## Execution order (ruthless)

```
W3 (CI honesty) ─┬─► W6 (Trust stack ops)
                 │
W1 (Postgres RLS) ─► W2 (Amend) ─► W5 (Spaces persist)
                 │
W4 (1b → 310) ────┬─► W8 (BIRD)     [after W4 floor]
                  └─► CRAG adapter    [optional floor only — not in top 12]

W7 (schema gate) ─► W11 (3GB scenarios)
W9 (C5→C8) ───────► C10 plausibility / C11 alias graph [later]

W10 (G2.6) ◄── BLOCKED on OpenVault step 2
W12 (P17) ─────── parallel when owner gates packaging
```

**Dropped from active queue (still valid, not now):** G2.5b commitment auto-close, P18 per-surface API docs booklet, column lineage (Spaces Phase 3), DuckLake→Postgres/MinIO (Phase 4), P20 Rust hot paths, P16 observability dashboard, netie-engine capability landing (rawknn) — all parked with conditions in `PARKING_LOT.md`.

---

## THROW_AWAY clarification (2026-07-31 mend)

Means **do not claim shipped / do not mix into the wrong demo / do not build before conditions**.  
Does **not** mean erase multi-app engine ideas. See `docs/ACTIVE.md` (engine-first).

| Item | Correct reading |
|------|-----------------|
| Pointer in DMS demo | Out of **DMS Spaces demo** — Pointer remains a **peer consumer** (`D:\Netie Clicks`) |
| respond.io / Closer | **Parked** P4/P9 vertical (RUMA), not engine core |
| Palantir full parity | **Parked** P1 — ontology patterns stay; full parity is FDE/consumer work |
| Amend-before-Postgres | **DMS build-order** supersession only |
| MemPalace / Mem0 / trained JEPA as shipped | Honesty: **not shipped** — still valid **engine memory / persistent-agent roadmap** |

---

## THROW_AWAY — important in old docs, wrong for 2026-07 orientation

These conflict with Spaces/sandbox/docker/engine-first north star. Do not resurrect without explicit owner reversal.

| Item | Why bin it | Where it lingers |
|------|------------|------------------|
| **Big-bang `netie-engine` ↔ `main` merge** | 327-file divergence; add/add conflicts. Land one gate at a time (option C→B), never rebase merge. | `PARKING_LOT.md` P15, old branch plans |
| **Dedicated DB copy for golden benchmarks** | Root cause was DuckDB exclusive lock on `dms_demo.duckdb`, fixed with `read_only=True`. Copy task is dead weight. | `NEXT_LANES.md` test rules (struck through in STATUS) |
| **Friday lakehouse wire-up as primary track** | Lakehouse seeded, warehouse sync, Studio shipped. Product lock moved to Spaces + Postgres amend. | `PARKING_LOT.md` P19 Friday P0 debt, old STATUS blocks |
| **Amend-first before Postgres** | Superseded by binding build order: Phase 0 Postgres → Phase 1 amend. | Pre-2026-07-29 amend-first docs |
| **MemPalace / Mem0 / trained JEPA as shipped** | Honesty violation. JEPA family gate exists; world-model training does not. | `NEXT_LANES.md`, `STATUS.md` 2026-07-29 honesty audit |
| **MinIO 500GB / 100TB analytics / petabyte lakehouse** | Spaces doc caps at 500GB–2TB fat node; 100TB = cold blobs, not one analytics system. | Old demo ambitions, `FOUNDATION_AUDIT` scale claims |
| **Excel bidirectional write-back** | Product law: Excel is source-only; emit generated export. | Any amend doc implying sync-back |
| **Thousands of connectors / CDC / streaming brokers** | Marketplace ambition, not demo. Brokers are **last** in build order. | `DMS_SPACES_PRODUCT` §6–7, `CURSOR_EXEC_PACKET` S2 broker |
| **CRAG as north star** | Document-index discipline floor only; optimizing CRAG ≠ governed NL→SQL product. Inventory done; adapter is regression, not strategy. | Phase 2 eval plan if promoted above BIRD/Spaces |
| **Pointer / Netie Clicks inside DMS demo** | External Act client. DMS demo = warehouse AI on Excel/DB only. | Mixed demo packets pre-2026-07-29 |
| **respond.io / Closer auto-reply (P4/P9)** | Different product lane; condition = paying partner. | `PARKING_LOT.md` P4/P9 |
| **Palantir ontology + AIP full parity (P1)** | Engine-first says vertical parity is consumer work; condition = paying clients. | `PARKING_LOT.md` P1, world-engine brief |
| **Firecracker / WASM production (P2)** | Fuel sandbox scaffold only; enterprise conversation not started. | Security handback, parking lot |
| **Third orchestrator / Temporal durable execution** | Racing-router + DAG stack is blessed; no third runtime. | `CURSOR_EXEC_PACKET`, research slices |
| **Problem-centric planner/coder/tester swarms** | Anthropic anti-pattern; keep orchestrator-centric presets. | `PARKING_LOT.md` P19 distill debts |
| **STATUS "Next: G2.3 OSR"** | G2.3–G2.5 shipped 2026-07-27. Stale table at bottom of STATUS. | `STATUS.md` "Next three moves" |
| **Raw SQL / execution-accuracy leaderboard tuning** | Customer sees DMS envelope; benchmarks without `assert_envelope_valid` certify the wrong layer. | Academic Spider 1.0, leaderboard-only BIRD runs |
| **Claim "0 wrong" at n=47** | Marketing hope, not statistics. Trust UI must stay red until N≥310 + wrong==0. | Any UI copy, eval_routes permissive `passed` check |

---

## One-line state (2026-07-31)

Ontology + Trust surfaces exist; live routing gaps largely closed; envelope E1–E8 path green at Phase 0. Product spine is **Postgres RLS → amend → Spaces sandbox**; engine spine is **C2-clean C7 + eval floor to 310**; adoption spine waits on **OpenVault → G2.6**.
