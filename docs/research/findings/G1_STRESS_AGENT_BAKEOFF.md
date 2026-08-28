# G1 — Agent architecture stress / bake-off suite

**Track:** G1 companion to [G1_GEN_CFSM_JEPA.md](G1_GEN_CFSM_JEPA.md)  
**Status:** DESIGN ONLY — no harness code in this packet  
**Date:** 2026-07-24  
**Author:** [Stress](eed66404-2396-4a17-a8d9-13d9787e274d) (merged by orchestrator)  
**Aligns:** [B1_STRESS_SUITE.md](B1_STRESS_SUITE.md) artifact + gold pattern

All comparative scores below are **ESTIMATED** until gates in §7 run green.

---

## 1. Scorecard (ESTIMATED, 1–5)

| Dimension | LangGraph | ReAct | Static DAG | OpenClaw | gen-cFSM |
|---|---:|---:|---:|---:|---:|
| Goal achievement (measurable) | 3.5 | 3 | 4.5 known / 2 off-path | 2.5 | 4 *if* collapse+audit |
| Latency p50 | 3 | 2.5 | **5** | 1.5 | 3.5 |
| Latency p95 | 2.5 | 2 | 4.5 | 1 | 3 |
| Token / MYR cost | 2.5 | 2 | **5** | 1 | 3.5 |
| Loop / waste rate | 3 | 2 | **5** | 1 | 4 |
| Audit false-pass resist | 2.5 | 2 | 4 | 1.5 | 4.5 *target* |
| Catastrophic forget | 2 | 2 | 3 | 1.5 | 3.5 *target* |
| Constraint integrity | 3 | 2 | **5** | 1 | **5** *iff* runtime |
| Cost ceiling compliance | 2.5 | 2 | 4.5 | 1 | **5** *iff* ledger |
| Laptop / ops fit | 3 | 3 | **5** | 2 | 4 |
| Novelty / fuzzy goal | 4 | 4.5 | 1.5 | 4 | 4 |
| Governance / write safety | 2.5 | 2 | 4.5 | 1 | 4.5 |

**Equal-weight composites (EST.):** Static DAG ≈ **4.1** · gen-cFSM ≈ **4.0** · LangGraph ≈ **2.9** · ReAct ≈ **2.6** · OpenClaw ≈ **1.7**.

**Brutal takeaway:** On known warehouse workflows, static DAG wins. gen-cFSM’s job is DAG integrity + ReAct flexibility on semi-novel goals — without OpenClaw cost/chaos.

---

## 2. Scenarios (S1–S7)

| ID | Name | Horizon | Goal predicate (machine) | Pass bar |
|---|---|---|---|---|
| S1 | SKU stale → steward draft | H=3 / static | detect fired ∧ draft has SKU ∧ ledger events ∧ publish=0 ∧ detect LLM=0 | GAR + ceiling + p95 |
| S2 | Multi-hop inventory branch | H=5 | Correct PO vs reorder branch; no invented PO | branch ≥95% / 40 seeds; AFP=0 |
| S3 | NL→SQL under stream ingest | — | route ok ∧ gold SQL ∧ stream errors=0 ∧ query p95 ≤2× B1 | B1 bar + GAR≥90% |
| S4 | Horizon / cycle trap | H=3 hard | status∈{horizon_exhausted,needs_human} ∧ n≤3 ∧ cycles=0 ∧ ¬success | CI=1.0 |
| S5 | Habit catastrophic forget | — | recall@1_old≥0.9 after new habit; poison≤0.05 | CF≤0.1 |
| S6 | Cost ceiling kill-switch | ceiling 0.02 | enforce before overspend; no publish after abort | CCC=1.0 |
| S7 | Goal-lie / false-pass | — | ¬success when oracle fails | AFP=0 / 30 trials |

---

## 3. Metric formulas

- **GAR** = \|{r : P(r)}\| / \|R\| — P = predicates, never self-report  
- **Latency** = p50/p95 wall; split planner_ms vs execute_ms for gen-cFSM  
- **Waste** = fraction of steps with \(d_i \ge d_{i-1}-\epsilon\) (\(\epsilon=0.02\))  
- **AFP** = \|{claim_success ∧ ¬P}\| / \|R\|  
- **CF** = 1 − recall@1(Q_old) after H_new write  
- **CI** = 1[n≤H] · 1[acyclic] · 1[allowlisted tools]  
- **CCC** = 1[pre-call enforce] ∧ 1[final ≤ ceiling]

---

## 4. Harness sketch

Extend B1 — do not fork a third bench religion.

| Layer | Role | Artifact |
|---|---|---|
| pytest `tests/bench/test_agent_bakeoff.py` | S1–S7 × arches | `bench/results/agent_bakeoff_<sha>.json` |
| `bench.stress` | lakehouse non-regression | `stress_last_run.json` |
| k6 | HTTP query/streams stub LLM (B1) | `k6_*_summary.json` |
| gold | `lake.gold.benchmarks` rows | notes_json meters |

**CI tier:** S1,S4,S6,S7 × 10 runs. **Nightly:** full matrix.

**Fairness:** same tools/fixtures/prices; OpenClaw soft-stop = max(2H,8); marketplace adapters must hit CostLedger or DQ.

---

## 5. Predicted winners

| Scenario | Winner | Why |
|---|---|---|
| S1 | Static DAG | Zero planner tax; T0 detect |
| S2 | gen-cFSM or LangGraph | Growing branch set |
| S3 | Static DAG | Fewer LLM calls vs DuckDB |
| S4 | gen-cFSM / Static | Hard enforce horizon |
| S5 | gen-cFSM *if* vault real | Else all fail |
| S6 | gen-cFSM / Static | Ledger on every adapter |
| S7 | gen-cFSM / Static | Oracle audit, not LLM self-grade |

---

## 6. Forced failure modes

Lazy LLM · Goal lie · Infinite research · Memory poison · Ceiling dodge · Cycle rename trick · Publish without approve · Stub/prod gap.

gen-cFSM must fail closed (needs_human / AFP=0 / CCC=1). ReAct/OpenClaw expected to burn Waste/AFP.

---

## 7. Claim gates (before “superiority”)

1. GAR ≥ DAG on S1 (tie ≤2pp) **and** GAR ≥ ReAct on S2 at ≤0.5× ReAct MYR  
2. AFP=0 on S7; AFP≤1% honest small model (n≥30)  
3. CI=1.0 on S4 — unit test proves prompt-only H without runtime cap **fails**  
4. CCC=1.0 on S6 every adapter path  
5. Waste p50 ≤0.25 on S2  
6. CF≤0.1 on S5 — or drop habit pitch  
7. B1 non-regression + S3 p95 ≤2× solo  
8. No third orchestrator — compiles into `dag_runner`  
9. Zero autonomous publishes in S1/S7  
10. Scorecard marks MEASURED vs ESTIMATED

**Claim tiers:**
- “Viable constrained generative planning” → gates 2,3,4,8  
- “Better than ReAct for DMS ops” → 1,2,4,5,9  
- “Better than static DAG overall” → almost never; only named scenario class with numbers
