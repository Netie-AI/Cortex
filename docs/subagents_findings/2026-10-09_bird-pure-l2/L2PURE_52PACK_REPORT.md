# Pure L2 text-to-SQL: final report

## Bottom line

- The AI model writes every query. There are no keyword maps, saved questions, templates or ranking fallbacks. On the held-out half of the 52-question DMS pack it now gets **20 of 26 right (76.9%)**, up from 15 of 26 (57.7%) at baseline. On the dev half it gets 22 of 26 (84.6%), up from 13 of 26 (50%).
- 5 of the 6 remaining wrong answers return the right values with a different set of columns than the answer key. The DMS comparator requires the column count to match exactly, so these count as wrong.
- The old DMS path scores 52 of 52 on the same pack, but only 4 of its 43 answers came from SQL the model wrote. The other 39 came from matching the question to a pre-built metric. It refuses the trap questions by matching their exact wording against a list in the code.
- Next step: put this lane into DMS `Executor.live_ask` in place of the ontology-ranking and keyword lane. Keep the read-only check, the grant check, EXPLAIN, abstain, Cortex submit and the Cortex ledger. Details are in section 9.

## 1. The pipeline (the AI writes all SQL, with no rules)

`l2.py` (sha e92dc2c777ab03bd) and `prompts/` (sha 4eb0d6754c7321da). For each question:

1. `answer()` receives only the question text and the tables the asking Space may read. The table list comes from DMS `demo_grants.py`: Finance gets locations, inventory, transactions and suppliers; Ops gets locations, inventory and shipments.
2. A schema context is built from the live database at run time: columns, types, keys, row counts, real values of small columns (dates shown with their age), and join keys inferred from the data. Tables whose names start with "_" (such as `_verified_queries`) are never shown.
3. The model plans step by step, then returns one DuckDB SELECT or `ABSTAIN: <reason>`.
4. The query is parsed with sqlglot. Only a single SELECT or WITH over granted tables is allowed, and file, system and catalog functions are banned.
5. EXPLAIN runs, then the query executes on a read-only copy of the warehouse with external access off and a 10 s timeout.
6. Any parse, scope or execution error goes back to the model, up to 2 retries.
7. Unusable replies are thrown away and asked again, up to 2 times. These are empty replies, leaked template tokens, word loops, and replies cut off at the token limit.
8. A review step shows the model its result and lets it confirm or fix the query. It revised 0 of 43 queries, so the next version should drop it.

Grading is separate from answering. Result rows are compared with the rows from the oracle SQL using DMS's comparator (`oracle_row_match`), plus a 1e-6 float tolerance. A trap counts as correct only if the pipeline abstains.

Model: NVIDIA `moonshotai/kimi-k3` at temperature 0, 1 vote, max_tokens 8192, calls at least 1.5 s apart. Every one of the 104 replies in the final run's trace was served by kimi-k3, with no fallback model.

## 2. Results, held-out first

Split: question ids are sorted, and every other id goes to dev or held-out. Only dev rows were used for tuning; held-out was reported only. "Traps handled" means the pipeline correctly refused or abstained (REFUSE_OK).

| Split, run | n | OK | WRONG | ABSTAIN | ERROR | Traps handled | Correct |
|---|---|---|---|---|---|---|---|
| **Held-out, baseline (r0)** | 26 | 12 | 10 | 0 | 0 | 3 of 4 | 15 (57.7%) |
| **Held-out, final (r2)** | 26 | 17 | 5 | 0 | 0 | 3 of 4 | **20 (76.9%)** |
| Dev, baseline (r0) | 26 | 11 | 8 | 2 | 0 | 2 of 5 | 13 (50.0%) |
| Dev, final (r2) | 26 | 19 | 1 | 1 | 0 | 3 of 5 | 22 (84.6%) |

- **Held-out, non-trap questions only:** 12 of 22 OK at baseline, 17 of 22 (77.3%) at the end.
- **Raw r2, before the completion rerun:** two questions got HTTP 429 five times in a row and never reached the model. Counting those as errors gives held-out 16 OK, 1 ERROR, 19 correct (73.1%), and dev 18 OK, 1 ERROR, 21 correct (80.8%). Section 3 has the details.
- **All 5 held-out gains** (WRONG to OK: cq_cctv_wh_a, cq_cold_storage, ops_cold_storage, ops_freight_spend_destination, ops_shipment_cost) are on questions that have no dev question with the same wording.
- **Rule of three:** no L2 split has WRONG = 0, at baseline or final, so the bound does not apply. The only WRONG = 0 results are the old DMS path's, in section 7.
- **Latency (final):** median 16.8 s on dev and 21.3 s on held-out; the slowest question took 262 s. Model calls: 54 on dev and 52 on held-out.
- **Noise:** each split has 26 questions, so one question moves the score by 3.8 points. Each round was run once, so run-to-run variance was not measured.

## 3. Questions that never reached a model (HTTP 429)

| Run | Never reached a model | HTTP 429 events |
|---|---|---|
| Baseline | 0 of 52 | 30 |
| r1 | 0 of 52 | 40 |
| r2 raw | 2 of 52: ops_sku_count_by_category (dev) and ops_cold_storage (held-out), 5 × 429 each | 65 |
| r2 final (merged) | 0 of 52, but the 2 rows above come from a rerun of only those 2 questions with the same code (flag `completed_after_api_refusal=true`; the original refusals are kept under `original_attempt`) | 55 |
| r3 (rejected) | 0 of 52; its longer backoff and adaptive pacing removed final refusals | 73 |

Garbled model replies are a second infrastructure problem. In r2, 9 of 106 calls were unusable and had to be asked again (6 dev, 3 held-out). In r1, cq_stock_value_worth got 5 garbled replies in a row and ended as ERROR.

## 4. What each round changed and whether it was kept

| Round | Change (all general, nothing per question) | Dev correct | Held-out correct | Kept |
|---|---|---|---|---|
| r0 baseline | Model writes SQL; retry with the DB error | 13 | 15 | n/a |
| r1 | (1) Schema marks KEY columns and the columns that refer to them; prompt asks for one identifier per item plus only the requested measures. (2) Unusable replies are discarded and asked again; an abstention must have the form `ABSTAIN: reason`. | 20 | 18 | Yes |
| r2 | (1) Small numeric and date columns list every value (dates with their age). The prompt has a "readings" step: try the usual cutoffs against the real values, answer if they agree, abstain if they disagree, and abstain on judgement-style questions. (2) Result review step. | 22 (raw 21) | 20 (raw 19) | Yes, with caveat |
| r3 | (1) Up to 3 candidates per question, majority vote by result. (2) Backoff of 4/8/16/32 s and adaptive pacing. | 21 | 20 | No, restored to r2 |

- **r2 caveat:** in the raw run, dev OK was 18, a tie with r1, which would mean restore. The keep depends on the completion rerun. Also, cq_audit_overdue was OK in only 1 of 2 runs with the same system prompt. Treat r2's gain as within noise.
- **r3:** its one dev change (trap_short_paraphrase answered instead of abstaining) was model variance. It used 98 dev calls instead of 54, and the median latency rose from 16.8 s to 70.6 s.

## 5. Anti-hard-coding audits

An independent audit ran after every round. All three found **no hard-coding**.

- **Checker.** `no_hardcode_check.py` passed before and after every run, and again in this step: exit 0 on `l2.py` and all 5 prompt files. It checks against 52 question ids and texts, 45 oracle SQL queries, 9 tables, 21 underscored columns and 44 stored values. Its self-test catches every leak planted in it.
- **Manual review.** The diffs of each round and greps for domain words (sku, supplier, audit, reorder, capacity, cctv, worry, last month and others) found only generic wording. There are no few-shot examples and no SQL from the pack.
- **Provenance, re-checked in this step:**
  - all 52 final answers (SQL or abstention) appear word for word in a model reply in the trace;
  - the l2.py and prompt hashes match the run files;
  - `l2.py` is byte-identical to `l2_r2.py`, and `prompts/` matches `prompts_r2/`.
- **Integrity.**
  - No NVIDIA or Gemini key value appears in any file under l2pure (grep printed counts only: 0).
  - `git status` is clean in both /home/user/Cortex and /home/user/dms.
- **Concerns the audits raised.** These are not hard-coding, but they matter:
  - The r2 abstain bullet on judgement questions is a general paraphrase of one dev trap (trap_short_paraphrase). In this pack it only ever fires on that question.
  - The r2 cutoff rule works because this warehouse is tiny (at most 15 rows per table), so the schema lists almost every value. On real-size tables the model would see only quartiles.
  - The output-shape rules follow the answer key's house style: keys rather than names, and few columns.
  - 8 of the 26 held-out questions have exactly the same wording as a dev question (asked in the other Space), so held-out partly re-tests dev.

## 6. Remaining failure classes, with 2 examples each

This section uses the final r2 results from both splits. Held-out rows were looked at only after tuning ended.

**A. Right values, different columns from the answer key.** This is 5 of the 6 WRONGs. The DMS comparator requires the exact column count.

- cq_capacity_above_90 (held-out; ops_capacity_above_90 fails the same way). "Which locations are above 90 percent capacity?"
  - Model SQL: `SELECT location_id, 100.0 * current_load_kg / capacity_kg AS utilization_pct FROM locations WHERE current_load_kg / capacity_kg > 0.9`. It returns WH-C and WH-E with their percentages.
  - The oracle selects only `location_code` (WH-C, WH-E), so the extra percentage column fails the match.
- ops_low_stock_wh_a (dev; cq_low_stock_wh_a in held-out fails the same way). "Which SKUs are below reorder level in warehouse A?"
  - Model SQL: `SELECT sku FROM inventory WHERE location_id = 'WH-A' AND quantity_kg < reorder_level_kg`. It returns RS622XKR, which is right.
  - The oracle also returns `quantity_kg` (80.0). The model follows the r1 rule "a which-items question returns only the identifier".
  - ops_cctv_wh_a is the same pattern: CAM-A-01 is right, but the oracle also returns location_code.

**B. A business definition the data does not store.** This is 1 WRONG and 1 ABSTAIN.

- cq_spend_by_country (held-out). "What is our total spend by supplier country?"
  - Model SQL: `SELECT s.country, SUM(t.quantity_kg * t.unit_cost_myr) ... FROM transactions t JOIN inventory i ON t.sku = i.sku JOIN suppliers s ON i.supplier_id = s.supplier_id WHERE t.txn_type = 'inbound' GROUP BY s.country`. It returns MY 2250 and SG 4200.
  - The oracle defines "spend" as the value of stock on hand (inventory quantity × unit cost): MY 20516, SG 7524, TH 1800.
  - The model read "spend" as purchases. Nothing in the data says which reading is meant.
- cq_supplier_ranking (dev). "Rank suppliers by combined risk and lead time score."
  - The model abstained: "no defined formula ... sum, product, weighted blend produce different rankings".
  - The oracle uses 0.65 × risk + 0.35 × lead_time/60, which no general method can find.

**C. Answered when it should have refused or abstained.** There are 3 REFUSE_MISSED in the final run, of two kinds.

- *Data can answer it; the pack refuses it only because it is not certified.* Example: trap_delayed_count (dev), "How many delayed incoming shipments per warehouse?"
  - Model SQL: `SELECT destination_location_id, COUNT(*) AS delayed_shipments FROM shipments WHERE status = 'delayed' GROUP BY destination_location_id`, which returns WH-D 1 and WH-A 1.
  - trap_how_full_synonym (dev, "how full is each warehouse") returned the correct utilisation per warehouse.
  - DMS refuses both by exact question text (`_UNCERTIFIED_PARAPHRASE` in `demo_pack.py`). A data-only pipeline cannot see that rule.
- *A real model error: answering a nearby question instead.*
  - trap_stock_by_bin (held-out), "Show stock by storage bin". The model wrote "no bin column exists" in its reasoning, then ran `SELECT location_id, SUM(quantity_kg) AS stock_kg FROM inventory GROUP BY location_id` anyway. The prompt says to abstain when a needed column is missing.
  - trap_short_paraphrase, "Are we short on anything the warehouse should worry about?". It abstained in r2, but in r3 it answered with `SELECT sku FROM inventory WHERE quantity_kg < reorder_level_kg`. This is run-to-run variance.

**D. Infrastructure.**

- ops_sku_count_by_category (r2 raw) got 5 × HTTP 429 and never reached the model.
- cq_stock_value_worth (r1) got 5 garbled replies in a row and ended as ERROR.

## 7. Comparison with the old DMS path

Source: `prove2/raw_final_armed.jsonl`. It used the same oracle file (byte-identical), the same warehouse contents (table-by-table hash match) and the same kimi-k3 model.

| | Old DMS path | Pure L2 (final) |
|---|---|---|
| Correct on the 52-question pack | 52 of 52 (43 answered and matched, 9 of 9 traps abstained) | 42 of 52 (dev 22, held-out 20) |
| Answers from SQL the model wrote | **4 of 43**: cq_spend_by_country, cq_stock_value_by_category, cq_sku_count, cq_sku_count_by_category | 39 of 39 answered with rows, plus every abstention |
| How the other answers were made | 39 via `ontology_ranking`: the question is mapped to a pre-built metric and compiled from the DMS ontology | none |
| How traps are caught | Exact question-text list (`_UNCERTIFIED_PARAPHRASE`) and the regex `worry about\|is this good\|just give me` (`_UNSURE_ASK` in `generative_ask.py`) | The model's own abstain decision |
| Held-out | 22 of 22 answered, 4 of 4 traps | 17 of 22 OK, 3 of 4 traps |
| Rule of three | WRONG = 0 with 43 answered: upper 95% bound about 3/43 = 7.0% (dev 3/21 = 14.3%, held-out 3/22 = 13.6%). The path was built around these exact questions, so the bound says nothing about new questions. | Not applicable, since WRONG > 0 |
| BIRD Mini-Dev (500 unseen questions) | 500 of 500 abstained, per the brief: 0 answered, so no rule-of-three bound | Not run yet |

The old path wins on this pack because the pack is what it was built around. The 3 traps the L2 path misses are exactly the 3 question texts the old code lists word for word.

## 8. Verified vs assumed

**Verified (re-checked from files in this step):**

- all tallies above, recomputed from the run JSONL files;
- the hashes;
- the no-hard-coding check (exit 0);
- all 52 final answers trace to model replies, all served by kimi-k3;
- both git trees are clean, and no key value appears in any file;
- the old path's 43 answered, 4 `generate_sql` and 39 `ontology_ranking`, and that its oracle and data match;
- the hard-coded trap lists exist in DMS;
- 8 of the 26 held-out questions repeat dev wording, and all 5 held-out gains are on questions without such a twin;
- `storage_bin` does not exist in this warehouse, so the pack's note on trap_stock_by_bin ("inventory.storage_bin exists") is out of date.

**Assumed or not verified:**

- *BIRD 500/500 abstain.* No run file for it is in this workspace, and the DMS changelog says not to quote a live Mini-Dev score from the tree. The number is taken from the brief.
- *Held-out was never used for tuning.* This is a process claim. The files are consistent with it but cannot prove it.
- *The results carry over to real data.* The warehouse has 7 tables and at most 15 rows per table, so the schema context shows nearly all of the data.
- *Run-to-run stability.* Every round was a single run.
- *The answer key's column conventions match what a user wants.*
- *The r2 gains are real.* The +2 on dev and +2 on held-out are within noise.

## 9. Recommended next step: make this the DMS answer path

1. **New module, DMS `packages/executor/dms_executor/l2_ask.py`.** Port from `l2.py`:
   - `build_schema_context`, `parse_reply` and `degenerate_reason`;
   - the retry-with-error loop;
   - the prompts, without `review.txt`.

   Its inputs are only the question and `Executor.grantable_tables(space_id)`.
2. **Wire it in, in DMS `packages/executor/dms_executor/__init__.py`, `Executor.live_ask`.**
   - Call `l2_ask` as the product lane in place of the `generative_ask` ontology-ranking, pack-overlay and keyword path.
   - Take `is_uncertified_paraphrase`, `_UNSURE_ASK` and the ranking overlay off the product path.
   - Keep `maybe_verified_ask` only for queries a user registers themselves (the `_verified_queries` table has 0 rows today). These are the user-made "mods", not code we ship.
3. **The gate keeps, reusing existing code:**
   - **Read-only check:** sqlglot single SELECT/WITH from `l2.py`, plus `reject_hostile_chat_sql` (`manifest.py`).
   - **Grant check:** cited tables must be in the Space grants (`validate_compiled_sql`).
   - **EXPLAIN:** `validate_compiled_sql`. On failure, the error goes back to the model, up to 2 retries.
   - **Execution:** through Cortex submit, so `CortexOS/execution/manifest.py` enforces scope fail-closed. Never a local DuckDB file in the product.
   - **Abstain:** a model `ABSTAIN: reason`, exhausted retries, unusable replies or an API refusal all give the `_abstain` envelope with badge ABSTAIN. None of them is ever a green badge.
   - **Ledger:** one append through Cortex (`dms_ledger.append_event`) with the question, SQL, model id, prompt_sha, schema_sha, row count and outcome.
   - **Badge:** model SQL that passes the gate is L2_VALIDATED, never L0.
4. **Harness.**
   - Adopt r3's backoff and adaptive pacing on their own; they change no answers.
   - Drop the review step.
   - Keep the unusable-reply guard.
5. **Open decision on the model call (owner).** Today the Cortex Insights request (`_insights_body` in `cortex_client/compute.py`) carries only the question and ontology, with no field for the L2 prompt or schema context. There are two options:
   - run the L2 planner engine-side, in Cortex Insights generate;
   - add a field to the contract, which is a contract minor version.

   Keys stay in OpenVault either way. A provider with fewer 429s and garbled replies (EPIC-HARNESS-ENT) would cut latency and errors.
6. **Acceptance before switching.**
   - Run the 52-question pack through `POST /v1/chat/ask` and score it with `score_curated` on the DMS envelope: badge, abstained, rows, audit_id and the rendered answer text.
   - Do 3 repeat runs to measure variance.
   - Then run BIRD Mini-Dev (`score_bird.py --minidev`) as the first test on unseen, larger data, with the rule-of-three line printed if WRONG = 0.
7. **Owner decisions this report cannot make:**
   - Relabel trap_delayed_count and trap_how_full_synonym (the data can answer them; they were refused only for certification), or accept them as misses.
   - Choose the comparator's column convention: exact count, or answer key columns contained in the answer. Set it in DMS rather than tuning prompts to it.
   - Where business definitions such as "spend" or the supplier score should live. A user-written glossary that is fed into the schema context as data would fit the "users create the mods" goal.

## Files

All under `/tmp/claude-0/-home-user-Cortex/61aaa8c0-8acb-59b6-a30a-94d9f33f9175/scratchpad/l2pure/`:

- Current version: `l2.py` and `prompts/` (= r2).
- Backups: `l2_r0.py`, `l2_r1.py`, `l2_r2.py`, `l2_r3_rejected.py`, `prompts_r0/`, `prompts_r1/`, `prompts_r2/`.
- Runs: `run_baseline.*`, `run_r1.*`, `run_r2.*` (final), `run_r2.raw.*`, `run_r3.*`, `runs/`.
- Checks: `no_hardcode_check.py`, `split.json`.
- Old DMS path run: `../prove2/raw_final_armed.jsonl`.

Note: REPORT.md was not written to `/tmp/claude-0/-home-user-Cortex/61aaa8c0-8acb-59b6-a30a-94d9f33f9175/scratchpad/l2pure/REPORT.md`, because the subagent harness blocks report files. The full report is the text above.