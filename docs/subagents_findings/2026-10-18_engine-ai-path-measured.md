# The engine's answer path measured with a live model (2026-10-18)

The founder ruled that benchmark measurement may use the provider keys in this container. Engine code is unchanged. FreeRoute reached the model through a local OpenVault stand-in that ran on loopback in scratch and was never committed. The stand-in pinned one model, forwarded chat to that provider, and approved the leave-machine gate for benchmark data. No key appears in any file.

## 1. The /dms/query engine path on bench/heldout (28 frozen items)
Held-out with L2 on and `codestral-2508` served through the stand-in (`python -m bench.heldout --engine --enable-l2`, plus the variants below, run in-process):

| Run | correct | abstained | incorrect |
|---|---|---|---|
| L2 off (rules only) | 1 | 27 | 0 |
| L2 on, engine as-is | 1 | 26 | 1 (must-abstain `ma_kuching_dark_store_2011` served by L2) |
| L2 on, `_shape_refusal` disabled | 1 | 13 | 14 |
| L2 on, `_shape_refusal` and `route_to_metric` disabled | 2 | 25 | 1 (same must-abstain) |

What each run shows (verified from the per-item envelope trace):
- **As-is:** the model is never asked about the 16 answerable SQL questions. In 15 of them the word-rule `_shape_refusal` abstains first: "nested comparison to a group aggregate", "stacked predicates", "nth-highest", "negated existence", "HAVING COUNT DISTINCT". The 16th is answered by a governed metric.
- **Shape refusal disabled:** the 12 new wrong answers do not come from the model. They are keyword-router answers (`route_to_metric`, layer `governed_metric`):
  - "DELAYED shipment" is served as `COUNT(*) FROM shipments WHERE status='DELAYED'` (1031) for three different questions;
  - "distinct SKUs" is served as `COUNT(DISTINCT sku) FROM inventory` (509) for two others.
  The shape refusal is a word rule that stops another word rule from serving wrong numbers.
- **Both rules disabled:** the model writes SQL for every question. Then the L2 plan gate (`CortexOS/dms/l2_plan_gates.py`) refuses 10 of the 16: "no certified metric plan matched the generated SQL; generated SQL is not a schema listing". On this path, model SQL is served only when it matches a certified query, fits a governed metric's columns and grain, or is a plain listing. A new analytical query is never served.

Two scorer defects inflate "incorrect":
- `missing columns [...]` counts a right value under another column alias as wrong;
- two items (`bird_over_capacity_with_critical_alert`, `bird_delayed_kg_exceeds_on_hand`) have canonical SQL that returns no rows on today's lake, because their gold says `txn_type = 'OUT'` and the lake now stores `outbound`.

## 1b. The plan+SQL ladder on the same 28 (POST /v1/contract/ask, CORTEX_PLAN_SQL=1)
Every demo table was granted and sent as `dms_payload`. Served rows were compared to the canonical SQL by value, ignoring column names. Model: codestral-2508.

| Run | answerable OK | answerable wrong | reconfirm or abstain | must-abstain served |
|---|---|---|---|---|
| main 52ac71f | 3 | 7 | 5 | 5 |
| plus LITERAL-GROUNDING-01 | 4 | 7 | 3 | 2 |

- **Without the check:** asked about `SKU-ZZZZZ` in `LOC-999`, the model filtered on `SKU-00026` and `LOC-001`, which came from the sampled values, and served their total. Every gate passed. With the check, the ladder feeds the identifier back and ends in reconfirm.
- **What the check catches:** only identifiers (letters with digits, or 3+ digits; not dates, times or period codes). On known-correct SQL it flags 2 of 500 BIRD gold queries (`K-8` and `K-9`, written as "Kindergarten to 8th grade") and 0 of 48 Cortex canonical queries.
- **Unchanged:** the remaining wrong answers are semantic, for example the wrong grain or a missed filter. A single run of 28 items cannot separate the other shifts from sampling noise.

## 2. BIRD Mini-Dev, prompt variants (split A = 150 fixed by hash; B = 350 kept for one final report)
EX means the predicted rows equal the gold rows as a set, as BIRD's evaluator scores it. Each change adds one technique:
- **v0:** DDL, evidence and question.
- **v1:** v0 plus an output contract (exactly the columns asked, in order, no extra ids or ranks).
- **v2:** v1 plus column descriptions and three sample values per column.
- **v3:** v2 plus an error- or empty-fed retry.

| Model | v0 | v1 | v2 | Paired v0→v2 |
|---|---|---|---|---|
| z-ai/glm-5.3 (reasoning) | 88/149 (59.1%) | 91/149 (61.1%) | 92/148 (62.2%) | +12 / −8 |
| codestral-2508 | 57/149 (38.3%) | (running) | 76/149 (51.0%) | +29 / −10 (sign test p ≈ 0.003) |

- **v3 on glm-5.3:** cut short by a provider 429. On its first 55 questions the retry ran once.
- **Remaining glm-5.3 errors under v2 (56):** 25 return a different column count from gold, and 15 return no rows.

## 3. BIRD Mini-Dev through Cortex's own AI path (split A, 150)
Each BIRD database was copied into DuckDB, and every question went through POST /v1/contract/ask with CORTEX_PLAN_SQL=1. The setup per database:
- a semantic layer listing its tables;
- a signed grant over them;
- `dms_payload` with BIRD's column descriptions and its foreign keys as joins;
- the question with BIRD's evidence appended as a note.

Served rows were scored against SQLite gold. Model: codestral-2508. No stronger route was configured, so the ladder ran plan, error-fed retries, then reconfirm.

| Tree | OK | wrong | reconfirm or abstain | EX | answered precision |
|---|---|---|---|---|---|
| main 8fc4505 | 57 | 71 | 22 | 38.0% | 44.5% |
| prompt fix (below) | 61 | 67 | 22 | 40.7% | 47.7% |

Paired, the prompt fix gained 12 and lost 8, which is not significant at n=150. Split B (350, held out) confirms it:

| Tree, split B | OK | wrong | reconfirm or abstain | EX | answered precision |
|---|---|---|---|---|---|
| main 8fc4505 | 145 | 137 | 66 | 41.7% | 51.4% |
| prompt fix | 166 | 120 | 62 | 47.7% | 58.0% |

Paired n=348: gained 38, lost 17, two-sided sign test p = 0.006.
- **Prompt fix:**
  - the plan and SQL system prompts no longer say "filter literals must come from the sampled values", which is what pushed the model to swap `SKU-ZZZZZ` for a sampled SKU;
  - filter values now come from the question, and the samples are used only to match spelling;
  - an output contract asks for exactly the columns requested.
- **Wrong answers under the fix (67):**
  - 10 have the right values with extra columns, so a scorer that checks only key columns would pass them;
  - 30 have the same shape but different values (semantic);
  - 13 have the right columns but a different row count;
  - 11 have extra columns and wrong values;
  - 3 are missing columns.
- **The same model with one SQL call** and v2 context (section 2) scores 51.0%. Cortex's plan-then-SQL path scores 38 to 41%.
- **#386's head (edda9cf)** folds the planner into one call per attempt. On split A it scores 46.7% EX (66 wrong, 14 reconfirm or abstain) and makes 1.16 model calls per question, against 2.22 on main:
  - paired against main: +23 / −10 (p = 0.035);
  - paired against the prompt fix: +24 / −15 (p = 0.20).
  It does not carry LITERAL-GROUNDING-01 or the prompt fix, and the effects look additive. So the one-call design is more accurate and half the model calls.

## Root-cause class
The answer path decides with word rules before the model and with stored-plan matching after it, so the model can only serve what the rules already cover. A word rule then becomes the only guard against another word rule's wrong answer. Retiring one rule lane without the others turns abstains into wrong answers.

## Invariants
- "Keyword cascades and word rules stay out of the answer path" (CLAUDE.md §9).
- "A wrong answer is never served" (WRONG=0).
- "Don't claim 0 wrong below n=300": 28 items bound nothing.
- "Assert the artifact the customer receives": every number above comes from the envelope.

## What follows
1. Retire `route_to_metric` as the serve chooser (#313). Do it together with `_shape_refusal`, or the shape refusal stays the only thing stopping wrong governed-metric numbers.
2. Replace the L2 plan gate's "matches a stored plan" rule with checks a novel query can pass, then measure WRONG on a held-out pack of at least 300 before serving:
   - the manifest, EXPLAIN and value-domain gates that already run;
   - the plausibility check;
   - the error- or empty-fed retry ladder that plan_sql already has.
3. Give the model's SQL prompt column descriptions, sample values from the granted tables, and an output contract. That is v2: +13 points EX on codestral, small on a reasoning model.
4. Fix the held-out scorer: compare values, not column names, and re-derive the two stale gold queries.

## Verified vs assumed
- **Verified:** every count above, from this session's runs; the per-item reasons, from the envelope trace; the location of the plan gate in code.
- **Assumed:**
  - that codestral and glm-5.3 behave like the production OpenVault tier (kimi-k3 is not served on this key);
  - that BIRD's gains carry over to DMS questions. Neither is measured on DMS's live 52.
