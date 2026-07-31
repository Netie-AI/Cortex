# DMS — Evaluation & Stress Test Plan

**Status:** execution plan · v0.1 · 2026-07-31
**Depends on:** `DMS_TECHNICAL_ARCHITECTURE.md` §20 · `DMS_ARCH_AMENDMENT_2026-07-30.md` §20
**Precondition:** Phase 0 must complete before any benchmark is run. Everything downstream is untrustworthy until it does.

---

## Phase 0 — Fix the measurement pipeline

**Nothing else in this document is valid until this phase passes.**

### 0.1 The envelope mapping defect

Symptom: Cortex abstains, prose says so, DMS envelope reports `badge=L2_VALIDATED` and leaves `abstained` unset.

Impact: a green badge on a non-answer. This is a confidently-wrong stamp by the product's own definition, and it is the single most severe class of defect in the system.

Root cause class: **the harness measures Cortex; the customer sees DMS.** C10 cannot catch defects in the layer between them.

### 0.2 The rule that prevents recurrence

> **Every gate must assert on the artifact the customer receives, at the layer the customer receives it from.**
>
> Cortex-side assertions are necessary and insufficient. Every answer-path property — badge, abstained, values, sources, drillthrough_token, audit_id — must additionally be asserted on the DMS envelope returned by `POST /chat/ask`.

Add to both `CLAUDE.md` files.

### 0.3 Envelope invariant suite

A property-based test over the DMS envelope. Not example-based — properties, checked on every answer produced by every phase below.

| # | Invariant |
|---|---|
| E1 | `abstained == true` ⟺ `badge == ABSTAIN`. Never one without the other. |
| E2 | `abstained == true` ⟹ `values[]` empty, `contributing_sources[]` empty, `drillthrough_token` null |
| E3 | `abstained == false` ⟹ `values[]` non-empty **and** `sql_used` non-empty **and** `audit_id` non-empty |
| E4 | every numeric literal in `text` appears in `values[]` by id — no orphan numbers in prose |
| E5 | `badge ∈ {L0_CERTIFIED, L1_GOVERNED_METRIC, L2_VALIDATED, L2_ANOMALOUS, ABSTAIN}` |
| E6 | `demo_fallback_used == true` ⟹ banner flag set in the response payload |
| E7 | `contributing_sources[]` non-empty ⟹ `drillthrough_token` non-null and verifies |
| E8 | `as_of` present and not in the future |

**Gate:** all eight hold on 100% of envelopes produced by every phase. Any violation is a P0 that stops the phase.

### 0.4 Also close in this phase

| Item | Action |
|---|---|
| Adversarial suite **skips** on DuckDB lock | Make it **fail**. A skipped test in CI is a failing test. |
| G6 abstains on "total revenue" | Not a bug — no governed metric exists. Define it in the semantic layer. Good demo moment for the promotion loop. |
| `v2.5.0` tag never cut | Cut it. `cortex-contract` wheel is unpublished, so a clean machine still cannot install DMS. P-DMS-25 is closed for you, open for a customer. |
| OpenVault on `feat/openfree-token-budget` | Merge to main or state why it lives on a branch. |

### Phase 0 prompt

> Implement the DMS envelope invariant suite at `tests/invariants/test_envelope.py`.
>
> First fix the mapping defect: Cortex abstention must produce `badge=ABSTAIN` and `abstained=true` in the DMS envelope. Find every path that constructs an envelope and route them through one constructor — an AST invariant should fail the build if an envelope dict is built anywhere except that constructor.
>
> Then implement E1–E8 above as property assertions in a reusable `assert_envelope_valid(envelope)` helper. Every existing answer-path test calls it. Every benchmark harness in the eval plan calls it on every response.
>
> Make `test_adversarial_benchmark.py` **fail** rather than skip when the warehouse is locked, with an error naming the holding PID.
>
> Add a governed metric for bare total revenue to close the G6 gap.
>
> Commit as `fix(envelope): single constructor, E1-E8 invariants, fail-on-lock`.

---

## Phase 1 — Corpus to 300

### 1.1 Why 300

Rule of three: zero errors in *n* trials bounds the true error rate at 3/n with 95% confidence.

| Cases, zero wrong | Honest claim |
|---|---|
| 19 *(today)* | error < 16% |
| 100 | error < 3% |
| **300** | **error < 1%** |
| 600 | error < 0.5% |

"0 confidently wrong" is a marketing claim only at 300+. Below that it is a hope with a small sample attached.

### 1.2 Composition target

Twelve categories — the eleven from §20 of the amendment plus value normalization, which was added after the `BETA`/`SKU-BETA` incident.

| Category | Target cases |
|---|---|
| Grain and fan-out | 30 |
| Null semantics | 25 |
| Silent dedup | 20 |
| Temporal | 35 |
| Rare-but-correct SQL | 30 |
| Unit and currency | 20 |
| Semantic ambiguity | 25 |
| Malay and code-switched | 30 |
| Must-abstain | 40 |
| Coercion | 15 |
| **Value normalization** | **30** |
| Fallback hazard | 10 |
| **Total** | **310** |

Must-abstain is the largest bucket on purpose. Abstention correctness is the invariant; answer coverage is a feature.

### 1.3 Generation strategy

Hand-writing 310 cases is weeks. Generate, then verify:

1. **Seed** — 3–5 hand-written golds per category, with known-correct answers computed independently of the system under test.
2. **Paraphrase expansion** — an LLM generates 8–10 paraphrases per seed. Paraphrases must preserve entities and numbers exactly; only phrasing varies.
3. **Mutation** — programmatically mutate seeds along the category axis: swap a join to create fan-out, inject a NULL, change `UNION ALL` to `UNION`, shift a date across a fiscal boundary, alter a literal's encoding.
4. **Human verification of the gold, not the prediction.** Every case's expected answer is verified by you or computed by an independent script. A corpus with wrong golds is worse than no corpus.

### 1.4 Ratchet

```yaml
# bench/thresholds.yaml — CI enforced
confidently_wrong: 0        # hard zero, never increases
correct_rate: 0.62          # floor, may only increase
abstain_rate: <= 0.38       # ceiling, may only decrease
```

Correct rate and abstain rate ratchet in your favour over time. `confidently_wrong` is not a ratchet — it is zero, permanently, and a single violation blocks merge.

---

## Phase 2 — CRAG

### 2.1 What it measures for DMS

CRAG scores correct `+1`, missing `0`, incorrect `−1`. It is the only major benchmark whose scoring rewards abstention over guessing — which makes it a direct external validation of the core invariant.

**Scope honesty:** CRAG exercises the document index (Tier 3): chunking, hybrid retrieval, RAG quoting. It does **not** exercise the analytical core, `_src` propagation, drill-through, or NL→SQL. It validates the *discipline*, not the *product*.

Use it as a regression floor on abstention calibration. Never as a north star — optimizing for CRAG optimizes document QA, which is not the wedge.

### 2.2 Verify what you have first

```bash
ls D:\DMS\tests\repos\CRAG
head -40 D:\DMS\tests\repos\CRAG\README.md
ls D:\DMS\tests\repos\open_ragbench
head -40 D:\DMS\tests\repos\open_ragbench\README.md
```

Several distinct projects share the "RAGBench" name. Confirm the schema, licence, and question format before building an adapter. Record findings in `docs/eval/BENCHMARK_INVENTORY.md` — question count, field names, scoring rubric, licence, and whether the retrieval corpus ships with it.

### 2.3 Adapter design

CRAG assumes a QA system. DMS assumes a Space with governed sources. The adapter bridges them:

```
CRAG retrieval corpus
  → ingest as documents into a dedicated Space (blob tier + doc index)
  → NEVER into silver. These are unverified web passages, not company facts.

CRAG question
  → POST /chat/ask  {question, space_id, ask_mode: live, demo_fallback: false}

DMS envelope
  → assert_envelope_valid()          ← Phase 0, on every single response
  → map to CRAG scoring:
        abstained == true      → MISSING   (0)
        answer matches gold    → CORRECT   (+1)
        answer differs         → INCORRECT (−1)
```

**The abstain → MISSING mapping is the whole point.** Get it wrong and you score your own design as failure.

### 2.4 The real deliverable — a calibration curve, not a score

A single CRAG number is close to meaningless for a system with a deliberate abstain policy. Produce instead:

- **Calibration curve** — sweep the confidence/badge threshold, plot correct-rate against abstain-rate. This shows the operating point you have chosen and what it costs.
- **False-premise subset** — reported separately. **Hard gate: 100% abstention.** A false-premise question answered confidently is the worst possible failure and CRAG hands you a ready-made test set for it.
- **Per-question-type breakdown** — simple, conditional, comparison, aggregation, multi-hop, set, post-processing, false-premise. Aggregation and multi-hop are the ones that predict behaviour on real customer data.

### 2.5 Expected result — set the expectation now

DMS will score **lower on correct-rate and near-zero on incorrect** compared to a normal RAG system. That is the design working, not a failure. Report it as a distinct operating point:

> *"On CRAG, DMS abstains on X% and is incorrect on Y%. Comparable systems answer more and are incorrect on Z%. We chose the trade."*

If Y is not near zero, that is a P0 and the phase stops.

### 2.6 Phase 2 prompt

> Build `bench/crag/` — a CRAG adapter for DMS.
>
> Read `D:\DMS\tests\repos\CRAG` and write `docs/eval/BENCHMARK_INVENTORY.md` documenting the actual schema, question count, scoring rubric, and licence **before writing any adapter code**. If the format differs from what this plan assumes, report the difference and stop rather than adapting silently.
>
> Ingest the CRAG retrieval corpus into a dedicated Space via the normal ingest path — blob tier and document index only. **Assert in a test that no CRAG content reaches any silver or gold table.** These are unverified passages, not company facts.
>
> Run every question through `POST /chat/ask` in live mode with `demo_fallback=false`. Call `assert_envelope_valid()` on every response; any violation aborts the run with the offending envelope dumped.
>
> Map to CRAG scoring with `abstained → MISSING`, not `INCORRECT`.
>
> Emit `bench/crag/results/<timestamp>.json` plus a markdown report containing: overall CRAG score, the calibration curve (correct vs abstain across thresholds), a per-question-type table, and the false-premise subset reported separately.
>
> **Hard gates:** false-premise abstention == 100%; incorrect-rate < 2%. Fail the run if either misses.
>
> Commit as `feat(bench): CRAG adapter with calibration curve and false-premise gate`.

---

## Phase 3 — Text-to-SQL benchmarks (higher value than Phase 2)

### 3.1 Why these matter more

C7-full just shipped: schema retrieval → FreeRoute generation → literal normalization → EXPLAIN gate. **No benchmark currently measures it.** CRAG cannot — it has no SQL.

| Benchmark | What it measures | Fit |
|---|---|---|
| **BIRD-SQL** | messy real-world DBs, external knowledge required, execution accuracy | **closest to DMS's actual job** |
| **Spider 2.0** | enterprise schemas, 1000+ columns, multi-step | tests schema retrieval under realistic width |
| Spider 1.0 | clean academic schemas | too easy — skip |

BIRD's "external knowledge" requirement maps directly onto the semantic layer: questions that need a business definition the schema alone does not carry. That is exactly what metrics.yaml and the alias graph exist for.

### 3.2 The adaptation that matters

Standard text-to-SQL scoring is execution accuracy — did the query return the right rows. DMS needs a third bucket:

```
correct    → executed, rows match gold
abstained  → refused to generate           ← must be scored separately
incorrect  → executed, rows differ         ← the number that must approach zero
```

Report all three. A system with 55% correct / 44% abstained / 1% incorrect is **more valuable to an SME** than one with 75% correct / 0% abstained / 25% incorrect, and no standard leaderboard will tell you that. Say so explicitly in the report.

### 3.3 Schema-width stress

The specific thing C7 must survive: schema retrieval at width. Run BIRD/Spider 2.0 databases at increasing schema sizes and record where retrieval degrades.

| Tables | Expected | Watch for |
|---|---|---|
| < 20 | full schema fits a prompt | baseline |
| 20–100 | retrieval starts mattering | recall of the right table |
| 100–500 | retrieval is the bottleneck | wrong-table selection, silent |
| 500+ | enterprise reality | latency, recall collapse |

**The failure to hunt: retrieving the wrong table and generating valid SQL against it.** It parses, it validates, it executes, it returns a plausible number, and it is wrong. Same shape as `BETA`/`SKU-BETA`. Add it to the corpus as its own category.

---

## Phase 4 — Scale rig

### 4.1 The 1000-workbook generator

From §20 of the amendment. Synthetic, realistic mess, **known ground truth**.

```
bench/scale/generate.py
  --workbooks 1000
  --rows-per 500..50000
  --mess-profile realistic
  --seed 42

Mess injected, at controlled rates:
  merged header cells · title rows above headers · trailing notes rows
  numbers stored as text with thousands separators · mixed date formats
  Malay column names · two tables stacked in one sheet
  near-duplicate files (v1, v2, FINAL, FINAL_v2)
  one sheet with no table at all
  column name drift across the family (Amount → Amt → Jumlah)
```

Ground truth is computed by the generator, independent of DMS. That independence is the entire value.

### 4.2 What it measures

| Metric | Target |
|---|---|
| Ingest throughput | files/min, and the shape of the degradation curve |
| Triage accuracy | ≥ 95% correct class across the five §17 classes |
| Quarantine correctness | every bad row quarantined with the *right* reason; zero silent drops |
| Aggregate correctness | computed total == generator's known total, exactly |
| Drill-through latency at 1000 contributors | < 3s to first render |
| Rollup correctness | §21 grouping and Pareto identify the injected outlier file |
| Memory ceiling | peak RSS vs the pool broker's budget |
| `_src` integrity | 100% of silver rows trace to a real source_ref and row |

### 4.3 The critical assertion

> **The sum computed by DMS equals the sum computed by the generator, exactly.**

Not approximately. Exact match on a known total across 1000 messy files, with every contributing row traceable. If that passes, the provenance spine is real. If it fails, the failure is diagnosable because you know the truth.

---

## Phase 5 — Concurrency and chaos

### 5.1 Concurrency

The architecture's single-writer constraint is a hypothesis until it is loaded.

| Test | Assertion |
|---|---|
| 20 concurrent asks | no lock timeout; latency degrades gracefully; all envelopes valid |
| 5 concurrent amend confirms, same table | exactly one applies; four get 409; advisory lock holds |
| Ask during promote | reads succeed against the pre-promote snapshot |
| Pool broker saturation | activation **refused** with a clear error, never OOM |
| Ingest during ask | no corruption, no partial-read answers |

### 5.2 Chaos — inject the failures you have already lived through

| Injection | Required behaviour |
|---|---|
| **OpenVault killed mid-session** | answers continue on cached JWKS; visible "keys offline" banner; no silent degradation |
| Cortex killed mid-ask | DMS returns a clear error; **never** falls back to demo |
| `kill -9` during promote | quarantine and silver consistent on restart; no half-written table |
| Disk full during ingest | ingest fails cleanly with an honest receipt; nothing silently truncated |
| **Drive detach** *(you have had this twice)* | on reattach: ledger chain verifies, catalog consistent, no orphan Parquet |
| Postgres restart mid-transaction | advisory locks released; no stuck proposals |
| Clock skew ±10 min | manifest expiry handled correctly; no accidental accept of an expired manifest |

The drive-detach test is not hypothetical. Run it deliberately, with a USB drive, before a customer does it accidentally.

---

## Phase 6 — Subagent red team

### 6.1 The pattern that worked

C3's five-agent adversarial round found two working escapes that a 90-case corpus missed. It also produced **one agent with fabricated output** — which is why the verification stage below is mandatory, not optional.

### 6.2 Fleet design

```
        ┌──────────────────────────────────────────┐
        │  6 ADVERSARY AGENTS — attack only        │
        │  Each gets ONE lens. No cross-talk.      │
        │  Output: claim + exact reproduction steps│
        └────────────────┬─────────────────────────┘
                         │  raw claims
        ┌────────────────▼─────────────────────────┐
        │  VERIFIER — a DIFFERENT agent            │
        │  Reproduces every claim on the LIVE stack│
        │  Unreproducible → DISCARDED, logged      │
        └────────────────┬─────────────────────────┘
                         │  confirmed only
        ┌────────────────▼─────────────────────────┐
        │  JUDGE — severity + root cause class     │
        │  Symptom vs cause; names the invariant   │
        └──────────────────────────────────────────┘
```

### 6.3 The six lenses

| Agent | Lens | Hunting for |
|---|---|---|
| A1 | **Manifest escape** | path escapes past C3 — new DuckDB functions, shadowing, nested CTEs |
| A2 | **Silent wrong answer** | anything that returns a plausible number that is wrong. Grain, nulls, dedup, encoding |
| A3 | **Envelope lies** | badge/text disagreement, orphan numbers, missing provenance — the Phase 0 defect class |
| A4 | **Provenance breaks** | drill-through under a *different* manifest, `_src` loss, stale source_ref |
| A5 | **Amend bypass** | apply without confirm, stale token accepted, concurrent double-apply |
| A6 | **Fallback and degradation** | any path where the system substitutes something weaker without saying so |

A3 is listed because the Phase 0 defect proves that class is unexplored. Expect it to find more.

### 6.4 Rules for the run

Non-negotiable, and they come from your own history:

1. **Adversaries never verify their own claims.** One fabricated output in the C3 round is the reason.
2. **Every claim reproduces on the live stack**, not by reading code. Code-reading produces plausible-sounding non-bugs.
3. **Unreproducible claims are logged as discarded**, with the agent named. Track per-agent precision across runs.
4. **Fix root causes, not symptoms.** C3's `UNNEST` escape was fixed at the name-binding level, not by special-casing `UNNEST`. That is the standard.
5. **Every confirmed finding becomes a permanent corpus case.** The corpus grows monotonically. Cases are never removed.
6. **A fix that makes the system refuse legitimate queries is a failure**, not a win. C3's second pass caught exactly this — `delim=';'` checked as a path, `generate_series()` caught by default-deny. A control that refuses real work gets switched off, and then protects nothing.

### 6.5 Phase 6 prompt

> Run a six-agent adversarial review against the live DMS + Cortex + OpenVault stack.
>
> Launch six independent adversary agents, one lens each: A1 manifest escape, A2 silent wrong answer, A3 envelope lies, A4 provenance breaks, A5 amend bypass, A6 fallback and degradation. Each produces claims with exact reproduction steps. **Adversaries do not verify their own claims and do not see each other's output.**
>
> Then a separate verifier agent reproduces every claim against the live stack. Claims that do not reproduce are discarded and logged with the originating agent named — track per-agent precision. Then a judge assigns severity and names the root cause class and the invariant that should have caught it.
>
> Fix confirmed findings at the root cause, never the symptom. After each fix, re-run the full corpus to confirm no legitimate query has become refused — a control that blocks real work is a failure, not a win.
>
> Every confirmed finding becomes a permanent case in `bench/corpus/`.
>
> Output `docs/eval/REDTEAM_<date>.md`: claims raised, confirmed, discarded, per-agent precision, severity distribution, root cause classes, and fixes with commit SHAs.

---

## Execution order and gates

```
Phase 0  envelope invariants + fail-on-lock + v2.5.0 tag     ← BLOCKING
   │
   ├─► Phase 1  corpus to 310                                ← statistical floor
   │      │
   │      ├─► Phase 2  CRAG           (abstention calibration)
   │      └─► Phase 3  BIRD / Spider 2.0  (the product)      ← higher value
   │
   ├─► Phase 4  1000-workbook scale rig
   ├─► Phase 5  concurrency + chaos
   └─► Phase 6  subagent red team                            ← after the others
```

Phase 6 runs last because red teams find more when there is more built to attack, and because every earlier phase generates corpus cases the red team can extend.

### Hard gates

| Gate | Threshold | Blocks |
|---|---|---|
| Envelope invariants E1–E8 | 100% | everything |
| Confidently wrong, corpus | **0**, at n ≥ 300 | sellable claim |
| CRAG false-premise abstention | 100% | Phase 2 sign-off |
| CRAG incorrect-rate | < 2% | Phase 2 sign-off |
| Scale rig aggregate | exact match to generator truth | provenance spine sign-off |
| Concurrent amend | exactly one apply | D1 |
| Chaos: OpenVault down | answers continue, banner visible | D1 |
| Chaos: Cortex down | clear error, **never** demo fallback | D1 |
| Red team | zero unfixed criticals | customer install |

### What gets reported to a customer

Only two numbers, and only after Phase 1 and 3:

> *"Measured on N=310 adversarial cases plus BIRD-SQL: zero confidently wrong answers. The system abstains on X% of questions rather than guess."*

Everything else is internal. Resist the urge to quote a CRAG leaderboard position — it measures a different product.

---

## Appendix — what to hand a reviewer

If you want an outside read on any phase, the highest-signal artifacts:

1. `bench/corpus/` — the full corpus with golds
2. One live envelope, verbatim JSON, from each of: L0, L1, L2, ABSTAIN
3. `docs/eval/BENCHMARK_INVENTORY.md` — what the benchmark repos actually contain
4. The Phase 2 calibration curve
5. Scale rig output: generator truth vs DMS computed, side by side
6. `docs/eval/REDTEAM_<date>.md` including the discarded-claims section

The discarded-claims section matters as much as the confirmed one. It is how you know the verifier is doing its job.
