# P2: which general checks turn wrong answers into refusals

BIRD Mini-Dev, run `bird_minidev_r2` (moonshotai/kimi-k3, frozen pure-L2 harness r2). Written 2026-10-09.
Data: `runs/bird_minidev_r2.jsonl` and `.trace.jsonl` (latest row per key), the DuckDB mirrors (read-only) and, for grading only, `gold_cache.jsonl`. Code and intermediate files: `scratchpad/p2/`.

## 1. Recommendation

**Run these as hard refusals. All of them are cheap result-shape checks on the executed answer:**

1. **Extra columns.** The result has more than one column, but the question has no "and", no comma and no "also / along with / together with / as well as".
2. **Measure on a list.** A question worded with which/what/who/name/list that has no measure word (count, number of, total, average, percent, how many, ...) but whose SQL outputs an aggregate.
3. **Percentage not a single value.** A percent/ratio/proportion/rate question without each/per/every that does not return exactly 1 row × 1 column.
4. **How-many with extra columns.** A how-many question without each/per that returns more than one column.
5. **Plural answered by one row.** A plural question (list, what are, which are, names of, all the ...s) without a measure word that returns exactly one row.
6. **Empty result.** Refuse an empty result, but do **not** refuse on NULL cells: on B all 3 OK the NULL rule cost were legitimate NULLs.

The exact regexes are in `p2/features.py` (`q_features`) and the rules in `p2/catchers.py`.

**What to expect.** The held-out estimate is the gate chosen on A and measured once on B (`conservative`: checks 1, 2, 3 and 5, empty-or-NULL, evidence-column-unused and joins ≥ 3). It catches 58/117 WRONG (49.6%) and refuses 13/119 OK (10.9%). Precision goes from 50.4% to 64.2% [56.7–71.2], on 165 of 236 answered. The six checks above are that gate with the parts that did not replicate on B removed (evidence columns, joins ≥ 3, the NULL half of empty-or-NULL) and with the how-many check added (it was in `balanced` and did replicate). On A, in sample, they catch 47/105 for 5/132 OK lost. On B they catch 52/117 (44.4%) for 3/119 OK lost (2.5%), with precision 64.1% [56.9–70.7] on 181 answered. Because this set was chosen with B in view, its B figures are a hypothesis for the next held-out run, not a held-out result.

**Do not run as refusals.** On B each of these is near 1:1 on its own, or adds about as many OK losses as wrongs on top of the shape checks:
- every process signal: re-asked unusable reply, review revised or kept, retries, many calls, latency, truncated reply;
- join on a pair that is not a declared FK or listed join key;
- row-count outliers, duplicate rows, constant columns, percentage scale;
- evidence value/formula/column checks;
- SQL complexity.

The self-consistency re-sample, one extra call per question, did not earn a place either. On the A sample it caught 4 WRONG for 3 OK lost, so it was not added. On the B sample it caught 5/32 WRONG for 4/53 OK lost. Most wrong answers re-sample to the same wrong rows.

**Two things the gate cannot fix:**
- **WRONG never reaches 0, so no 3/answered bound exists.** Even every eligible catcher at once leaves 23 WRONG among 79 answered on B, after refusing half the right answers. The truly-wrong answers that survive are clean 1×1 results with a semantic error (what is counted, which filter reading, which formula). Only a semantic check can catch them. The next candidate is a grain probe that compares COUNT with COUNT(DISTINCT), untested so far.
- **Half of what the shape checks catch is right values in the wrong shape.** Of the 52 WRONG the six checks catch on B, 26 have the right values with extra, fewer or key-instead-of-name columns. The frozen prompt asks for the ranking measure and the key, and BIRD's gold wants neither. For a product user these are right answers being refused: under a convention-tolerant count the six checks refuse 29 right-value answers on B. Align the prompt's output contract with the check, or answer a failed shape check by re-asking with the defect named, rather than with a bare refusal.

**Wrong answers by kind** (section 3). Of the 222 WRONG, 74 are grading convention: the values are right, but there are extra, fewer or reordered columns, the key instead of the name, or a ×100 difference. 32 are partial, and 116 are truly wrong numbers or rows. In B the split is 39 / 15 / 63 of 117.

## 2. Method

- **Population.** All 498 finished questions were split 50/50 by sorting the keys on sha256(key) (hex) and taking the first 249 as A and the rest as B. A has 237 answered (132 OK, 105 WRONG), B has 236 answered (119 OK, 117 WRONG). ABSTAIN and ERROR are already refusals and are left out of every count below.
- **Signals a gate can see (no gold).** The question and evidence text (wording classes only), the final SQL (sqlglot), its full result (I re-executed every final SQL on its mirror, read-only, because the run file keeps only 50 rows; row counts reproduce on 470 of 473, and the other 3 are the answers above the run's 10,000-row cap, whose full counts match the grader's), the run's own process record (attempts, re-asked unusable replies, review outcome, calls, latency, `finish_reason`), the trace text of the final answer and review, the schema's declared FKs and its JOIN KEYS list, and two probes on the mirror: a LIMIT-tie probe (re-run each `ORDER BY ... LIMIT n` with the sort keys as extra columns and `LIMIT n+1`; a tie is row n+1 equal to row n on the keys) and the self-consistency re-sample of section 7.
- **General only.** No rule names a database, table, column, value or question. The wording classes are regexes such as `how many`, `percent|ratio|rate`, `each|per|every`, `list|which|name`.
- **Choice on A only.** 28 catcher families, 1–6 variants each (80 variants). For each family the variant with the highest utility on A, U = WRONG caught − 2 × OK lost, was kept, and a family needs at least 3 WRONG caught on A to be eligible. Combinations were built greedily on A from the kept variants, adding the family with the best marginal U while it adds at least 2 WRONG and: `conservative` marginal WRONG ≥ 2 × marginal OK lost **and** one-sided binomial p < 0.05 that the newly flagged set is more often WRONG than the remaining pool; `balanced` marginal WRONG ≥ 2 × OK lost; `max recall` marginal WRONG > OK lost (it picked the same nine as `balanced`, so it is not shown separately).
- **B touched once.** The A-only pass printed and wrote nothing about B (`A_ONLY=1 python3 catchers.py`); the choice was written to `frozen_offline.json`; then one full run produced the B numbers. After that I read B cases only to describe them (sections 3–6). Rows marked post-hoc (the five- and six-check sets in sections 1 and 5) were chosen with B in view. One disclosure: while building the gold-side classifier of section 3, before the choice was frozen, I read about 20 WRONG answers drawn at random from both halves. I did not look at their catcher signals.
- **Measures.** WRONG caught / WRONG (recall on wrongs); OK lost / OK (false refusals); after the gate: answered, OK, WRONG, precision = OK/answered with a Wilson 95% interval; 3/answered only where WRONG = 0. Grading is the run's own BIRD set rule (`verdict`), unchanged.
- **Baseline.** A precision 55.7% [49.3–61.9]; B precision 50.4% [44.1–56.7].

## 3. Which wrong answers are grading convention and which are wrong numbers

Every WRONG was compared with its gold result (reporting only; no catcher reads this). The class is the first that applies, in table order. Column matching allows any column order and the run's 1e-6 numeric tolerance.

- **Grading convention (74 of 222; 39 of B's 117): the values are right.** Mostly extra columns (49): the answer carries the ranking measure, the id next to the name, or a count next to the item. The frozen prompt asks for exactly that (step 6: "top N by X returns the item and X", "use its key"), and BIRD's gold does not. A further 12 answer with the entity's key where gold gives its name (or the reverse); I checked each by translating the answer's column through the mirror along its sqlglot lineage or declared FK and comparing with gold. 2 differ only by ×100 or rounding.
- **Partial (32; 15 in B).** Half or more of gold's columns match but others differ (11), or the rows are a strict subset (7: typically `LIMIT 1` on a tie) or superset (14) of gold's.
- **Truly wrong (116; 63 in B).** Different numbers or different rows (112) or an empty result (4). Reading B's leftovers, most are semantic: counting distinct patients where gold counts lab rows (or the reverse), a different filter reading, a different formula. A few may be gold-side (BIRD's evidence and gold have known errors), and 3 are on questions where the DuckDB mirror itself differs from gold (P1 section 3). I did not adjudicate them.

| Class | Group | Meaning | all (222) | A (105) | B (117) |
|---|---|---|---|---|---|
| extra_cols | convention | right rows; extra columns (some choice of the answer's columns set-equals gold) | 49 | 21 | 28 |
| fewer_cols | convention | right rows; fewer columns (the answer set-equals a choice of gold's columns) | 5 | 3 | 2 |
| reordered_cols | convention | same columns, different order | 6 | 3 | 3 |
| id_vs_name | convention | same entities named by another column of the same entity (key vs name), checked by translating through the mirror via the column's lineage / declared FK | 12 | 7 | 5 |
| scale_round | convention | numbers equal after x100 or /100, or one side rounded | 2 | 1 | 1 |
| mixed_cols | partial | at least half of gold's columns match as a projection, the rest differ | 11 | 7 | 4 |
| row_subset | partial | answer rows are a strict subset of gold rows (e.g. LIMIT 1 on a tie) | 7 | 3 | 4 |
| row_superset | partial | answer rows are a strict superset of gold rows | 14 | 7 | 7 |
| wrong_values | truly wrong | none of the above: different numbers / different rows | 112 | 52 | 60 |
| empty | truly wrong | answer returned 0 rows (gold never empty) | 4 | 1 | 3 |
| **convention total** | | | **74** | **35** | **39** |
| **partial total** | | | **32** | **17** | **15** |
| **truly wrong total** | | | **116** | **53** | **63** |

## 4. Each catcher on its own (rule chosen on A, reported on B)

B baseline: 236 answered, 117 WRONG, precision 50.4%. "truly-wrong caught" counts only B's 63 truly-wrong answers. A row's B columns are that catcher alone.

- **Held up on its own** (at least 3 WRONG and at least 2 WRONG per OK lost, on A and again on B; WRONG/OK lost): shape_howmany (3/0 on A, 4/1 on B); shape_single_measure (8/0 on A, 7/1 on B); shape_list_measure (15/1 on A, 16/0 on B); shape_extra_cols (29/4 on A, 31/3 on B); single_row_plural (5/0 on A, 4/0 on B); multi_row_singular (5/1 on A, 6/2 on B); empty_or_null (6/0 on A, 7/3 on B); truncated_reply (5/2 on A, 6/3 on B); doubt_in_reasoning (8/3 on A, 11/2 on B); evidence_columns (9/1 on A, 11/5 on B); limit_ties (13/4 on A, 12/2 on B); limit_present (28/10 on A, 35/6 on B); identifier_output (13/6 on A, 17/3 on B).
- **Did not hold up on its own:** percent_scale (2/3 on A, 1/4 on B); zero_or_negative (3/2 on A, 5/2 on B); duplicate_rows (1/3 on A, 0/5 on B); constant_column (8/2 on A, 7/4 on B); rowcount_outlier (5/4 on A, 6/6 on B); retried (2/1 on A, 2/4 on B); resampled (19/18 on A, 15/15 on B); review_changed (14/4 on A, 9/8 on B); many_calls (3/3 on A, 2/5 on B); slow (7/5 on A, 4/3 on B); evidence_values (4/5 on A, 3/2 on B); topn_rowcount (2/0 on A, 1/0 on B); evidence_formula (11/7 on A, 11/7 on B); join_without_fk (10/7 on A, 18/13 on B); sql_complexity (8/4 on A, 3/2 on B).
- **Process signals do not separate right from wrong.** Re-asked unusable reply, review revised, retries, calls ≥ 4 and latency are all near 1:1 or worse on B. The review step is the clearest case: 14/4 on A, 9/8 on B. These signals mark questions that were hard to answer, not answers that are wrong.
- **Holding up alone is not enough.** The LIMIT/tie, any-LIMIT, tie-in-reasoning, id-only-output, multi-row and truncated-reply checks flag mostly answers the shape checks already flag. On top of the `conservative` gate, each adds no more wrongs than OK losses on A (table in section 5). On B only the id-only-output check adds clearly more (7 WRONG, 2 OK), so it is the one worth re-testing.
- **Empty-or-NULL is mixed.** The empty-result part is sound. The chosen variant also refuses on any NULL cell, and all 3 OK it lost on B are legitimate NULLs (cards with no power or mana cost, drivers with no code).
- **About half of what the shape checks catch is right values.** Of the 31 WRONG the extra-column check catches on B, 19 are convention. All 4 the how-many check catches are convention.

| # | Catcher (rule chosen on A) | A: WRONG caught / OK lost | B: WRONG caught (recall) | B: truly-wrong caught | B: OK lost | B: answered | B: WRONG | B: precision [95%] | B caught: convention / partial / truly wrong | chosen on A? |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | **shape_howmany**: howmany & !grouped & ncols>1 | 3 / 0 | 4/117 (3.4%) | 0/63 | 1/119 (0.8%) | 231 | 113 | 51.1% [44.7–57.5] | 4 / 0 / 0 | balanced |
| 2 | **shape_single_measure**: percent & !grouped & !1x1 | 8 / 0 | 7/117 (6.0%) | 5/63 | 1/119 (0.8%) | 228 | 110 | 51.7% [45.3–58.2] | 2 / 0 / 5 | conservative + balanced |
| 3 | **shape_list_measure**: list/which & !aggword & agg output | 15 / 1 | 16/117 (13.7%) | 10/63 | 0/119 (0.0%) | 220 | 101 | 54.1% [47.5–60.6] | 5 / 1 / 10 | conservative + balanced |
| 4 | **shape_extra_cols**: ncols>1 & no and/comma/also | 29 / 4 | 31/117 (26.5%) | 11/63 | 3/119 (2.5%) | 202 | 86 | 57.4% [50.5–64.0] | 19 / 1 / 11 | conservative + balanced |
| 5 | **single_row_plural**: plural-ask & nrows==1 & !aggword | 5 / 0 | 4/117 (3.4%) | 1/63 | 0/119 (0.0%) | 232 | 113 | 51.3% [44.9–57.6] | 1 / 2 / 1 | conservative + balanced |
| 6 | **multi_row_singular**: the-superlative & nrows>1 | 5 / 1 | 6/117 (5.1%) | 5/63 | 2/119 (1.7%) | 228 | 111 | 51.3% [44.9–57.7] | 1 / 0 / 5 | no |
| 7 | **percent_scale**: percent & (fraction or >100) | 2 / 3 | 1/117 (0.9%) | 1/63 | 4/119 (3.4%) | 231 | 116 | 49.8% [43.4–56.2] | 0 / 0 / 1 | no (under 3 WRONG on A) |
| 8 | **empty_or_null**: empty \| any NULL cell | 6 / 0 | 7/117 (6.0%) | 5/63 | 3/119 (2.5%) | 226 | 110 | 51.3% [44.8–57.8] | 1 / 1 / 5 | conservative + balanced |
| 9 | **zero_or_negative**: single 0 \| negative | 3 / 2 | 5/117 (4.3%) | 3/63 | 2/119 (1.7%) | 229 | 112 | 51.1% [44.6–57.5] | 2 / 0 / 3 | no |
| 10 | **duplicate_rows**: dup rows | 1 / 3 | 0/117 (0.0%) | 0/63 | 5/119 (4.2%) | 231 | 117 | 49.4% [43.0–55.8] | 0 / 0 / 0 | no (under 3 WRONG on A) |
| 11 | **constant_column**: const col (>=2 rows) | 8 / 2 | 7/117 (6.0%) | 1/63 | 4/119 (3.4%) | 225 | 110 | 51.1% [44.6–57.6] | 4 / 2 / 1 | no |
| 12 | **rowcount_outlier**: nrows>200 | 5 / 4 | 6/117 (5.1%) | 5/63 | 6/119 (5.0%) | 224 | 111 | 50.4% [44.0–56.9] | 1 / 0 / 5 | no |
| 13 | **retried**: attempts>1 | 2 / 1 | 2/117 (1.7%) | 1/63 | 4/119 (3.4%) | 230 | 115 | 50.0% [43.6–56.4] | 1 / 0 / 1 | no (under 3 WRONG on A) |
| 14 | **resampled**: resamples>0 | 19 / 18 | 15/117 (12.8%) | 7/63 | 15/119 (12.6%) | 206 | 102 | 50.5% [43.7–57.2] | 6 / 2 / 7 | no |
| 15 | **review_changed**: revised | 14 / 4 | 9/117 (7.7%) | 4/63 | 8/119 (6.7%) | 219 | 108 | 50.7% [44.1–57.2] | 2 / 3 / 4 | no |
| 16 | **truncated_reply**: finish=length | 5 / 2 | 6/117 (5.1%) | 3/63 | 3/119 (2.5%) | 227 | 111 | 51.1% [44.6–57.5] | 2 / 1 / 3 | no |
| 17 | **many_calls**: calls>=4 | 3 / 3 | 2/117 (1.7%) | 1/63 | 5/119 (4.2%) | 229 | 115 | 49.8% [43.4–56.2] | 1 / 0 / 1 | balanced |
| 18 | **slow**: latency>90s | 7 / 5 | 4/117 (3.4%) | 3/63 | 3/119 (2.5%) | 229 | 113 | 50.7% [44.2–57.1] | 1 / 0 / 3 | no |
| 19 | **doubt_in_reasoning**: mentions tie | 8 / 3 | 11/117 (9.4%) | 4/63 | 2/119 (1.7%) | 223 | 106 | 52.5% [45.9–58.9] | 6 / 1 / 4 | no |
| 20 | **evidence_columns**: identifier-like column missing | 9 / 1 | 11/117 (9.4%) | 7/63 | 5/119 (4.2%) | 220 | 106 | 51.8% [45.2–58.3] | 3 / 1 / 7 | conservative + balanced |
| 21 | **evidence_values**: compared number missing | 4 / 5 | 3/117 (2.6%) | 2/63 | 2/119 (1.7%) | 231 | 114 | 50.6% [44.2–57.0] | 0 / 1 / 2 | no |
| 22 | **limit_ties**: tie at any LIMIT \| reasoning mentions tie | 13 / 4 | 12/117 (10.3%) | 4/63 | 2/119 (1.7%) | 222 | 105 | 52.7% [46.1–59.2] | 7 / 1 / 4 | no |
| 23 | **limit_present**: any LIMIT | 28 / 10 | 35/117 (29.9%) | 17/63 | 6/119 (5.0%) | 195 | 82 | 58.0% [50.9–64.7] | 16 / 2 / 17 | no |
| 24 | **topn_rowcount**: topN & nrows!=N | 2 / 0 | 1/117 (0.9%) | 1/63 | 0/119 (0.0%) | 235 | 116 | 50.6% [44.3–57.0] | 0 / 0 / 1 | no (under 3 WRONG on A) |
| 25 | **identifier_output**: any id col & asks name/who | 13 / 6 | 17/117 (14.5%) | 5/63 | 3/119 (2.5%) | 216 | 100 | 53.7% [47.0–60.2] | 10 / 2 / 5 | no |
| 26 | **evidence_formula**: ev divide/percent & SQL no division | 11 / 7 | 11/117 (9.4%) | 4/63 | 7/119 (5.9%) | 218 | 106 | 51.4% [44.8–57.9] | 7 / 0 / 4 | no |
| 27 | **join_without_fk**: not in JOIN KEYS list | 10 / 7 | 18/117 (15.4%) | 14/63 | 13/119 (10.9%) | 205 | 99 | 51.7% [44.9–58.5] | 2 / 2 / 14 | no |
| 28 | **sql_complexity**: joins>=3 | 8 / 4 | 3/117 (2.6%) | 2/63 | 2/119 (1.7%) | 231 | 114 | 50.6% [44.2–57.0] | 1 / 0 / 2 | conservative + balanced |

Variants tried on A (WRONG caught / OK lost on A; the chosen one maximised WRONG − 2·OK lost with at least 3 WRONG):

| Catcher | Variants tried on A: WRONG caught / OK lost |
|---|---|
| shape_howmany | `howmany & !grouped & !1x1` 3/1; `howmany & !grouped & ncols>1` 3/0; `howmany & !grouped & nrows!=1` 1/1; `count-word & !grouped & !1x1` 9/3 |
| shape_single_measure | `percent & !grouped & nrows!=1` 4/0; `aggword & !grouped & nrows!=1` 13/5; `percent & !grouped & !1x1` 8/0 |
| shape_list_measure | `list/which & !aggword & agg output` 15/1; `superlative & !aggword & ncols>1 & numeric col` 18/3; `list/which & !aggword & ncols>1 & numeric col` 27/9; `superlative & !aggword & ncols>1` 19/3 |
| shape_extra_cols | `ncols > 1+and+commas` 36/8; `ncols > 2+and+commas` 5/0; `ncols>1 & no and/comma/also` 29/4; `ncols>=3 & no also` 13/3 |
| single_row_plural | `plural-ask & nrows==1` 8/6; `list-start & nrows==1` 10/8; `plural-ask & nrows==1 & !aggword` 5/0 |
| multi_row_singular | `the-superlative & nrows>1` 5/1; `the-superlative & !grouped & nrows>1` 5/1 |
| percent_scale | `percent & (fraction or >100)` 2/3; `percent & 1x1 & no 100 in SQL` 1/3 |
| empty_or_null | `empty` 1/0; `empty \| all NULL` 1/0; `empty \| a column all NULL` 2/0; `empty \| any NULL cell` 6/0 |
| zero_or_negative | `single 0` 1/0; `negative` 2/2; `single 0 \| negative` 3/2 |
| duplicate_rows | `dup rows` 1/3 |
| constant_column | `const col (>=2 rows)` 8/2 |
| rowcount_outlier | `nrows>10` 15/16; `nrows>20` 13/13; `nrows>50` 10/9; `nrows>100` 5/6; `nrows>200` 5/4; `nrows>1000` 1/3 |
| retried | `attempts>1` 2/1; `any exec error` 2/1 |
| resampled | `resamples>0` 19/18 |
| review_changed | `revised` 14/4; `kept (...)` 5/4; `revised \| kept` 19/8; `review text names a defect` 17/9 |
| truncated_reply | `finish=length` 5/2 |
| many_calls | `calls>=3` 19/18; `calls>=4` 3/3 |
| slow | `latency>40s` 30/28; `latency>60s` 12/15; `latency>90s` 7/5 |
| doubt_in_reasoning | `mentions tie` 8/3; `ambiguity not negated` 10/10; `mentions tie \| ambiguity` 16/13 |
| evidence_columns | `identifier-like column missing` 9/1; `any named column missing` 24/22; `>=2 named columns missing` 8/4 |
| evidence_values | `quoted value missing` 11/9; `compared number missing` 4/5; `either missing` 14/14 |
| limit_ties | `tie at top-level LIMIT` 5/2; `tie at any LIMIT` 5/2; `tie at any LIMIT \| reasoning mentions tie` 13/4 |
| limit_present | `top-level LIMIT 1` 20/8; `any LIMIT` 28/10; `LIMIT without superlative` 1/1 |
| topn_rowcount | `topN & nrows!=N` 2/0; `topN & nrows>N` 0/0 |
| identifier_output | `asks name/who & only id cols` 1/3; `which/list & only id cols` 8/9; `any id col & asks name/who` 13/6 |
| evidence_formula | `ev divide/percent & SQL no division` 11/7; `ev *100/percent & SQL no 100` 1/0; `either` 11/7 |
| join_without_fk | `not declared FK` 14/11; `not in JOIN KEYS list` 10/7 |
| sql_complexity | `joins>=2` 17/19; `joins>=3` 8/4; `subquery/CTE` 25/13; `window fn` 2/0; `CASE` 14/21 |

## 5. Combinations (chosen on A, reported on B)

- **`conservative` (7 checks)** on B: catches 58/117 WRONG (49.6%), of which 29 of the 63 truly wrong; loses 13/119 OK (10.9%); answered 165, WRONG 59, precision 64.2% [56.7–71.2], up from 50.4%.
- **`balanced` (9 checks)** on B: catches 62/117 (53.0%); loses 18/119 (15.1%); answered 156, WRONG 55, precision 64.7% [57.0–71.8]. Its two extra steps (calls ≥ 4, and the how-many check) split: how-many replicated (3/0), calls ≥ 4 did not (1/5).
- **Shrinkage A → B.** In-sample on A the same gates reach 71.9% and 73.9%; on B 64.2% and 64.7%. The steps that did not replicate on B were empty-or-NULL (3/3), evidence columns (5/5), joins ≥ 3 (2/2) and calls ≥ 4 (1/5).
- **Post-hoc, B-informed (not a held-out number).** The five shape checks whose B marginal also stayed ≥ 2:1 catch 51/117 and lose only 3/119 OK, for a precision of 63.7% [56.5–70.4] on 182 answered. Adding the empty-result rule gives the six recommended checks: 52/117 caught, 3/119 lost, precision 64.1%. They catch 23 of the 63 truly-wrong answers and 26 convention ones. Because the set was picked using B, its B figures are optimistic, and the next held-out run has to confirm them.
- **Floor of offline checks:** every eligible catcher at once still leaves 23 WRONG among 79 answered on B (catching 94/117 but refusing 63/119 OK).

| Gate | Split | WRONG caught (recall on wrongs) | truly-wrong caught | OK lost | answered | OK | WRONG | precision [95%] | 3/answered |
|---|---|---|---|---|---|---|---|---|---|
| no gate (baseline) | A | 0/105 (0.0%) | 0/53 | 0/132 (0.0%) | 237 | 132 | 105 | 55.7% [49.3–61.9] | n/a (WRONG > 0) |
| no gate (baseline) | B | 0/117 (0.0%) | 0/63 | 0/119 (0.0%) | 236 | 119 | 117 | 50.4% [44.1–56.7] | n/a (WRONG > 0) |
| conservative | A | 57/105 (54.3%) | 19/53 | 9/132 (6.8%) | 171 | 123 | 48 | 71.9% [64.8–78.1] | n/a (WRONG > 0) |
| conservative | B | 58/117 (49.6%) | 29/63 | 13/119 (10.9%) | 165 | 106 | 59 | 64.2% [56.7–71.2] | n/a (WRONG > 0) |
| balanced | A | 62/105 (59.1%) | 22/53 | 10/132 (7.6%) | 165 | 122 | 43 | 73.9% [66.8–80.0] | n/a (WRONG > 0) |
| balanced | B | 62/117 (53.0%) | 30/63 | 18/119 (15.1%) | 156 | 101 | 55 | 64.7% [57.0–71.8] | n/a (WRONG > 0) |
| every eligible catcher (floor) | A | 81/105 (77.1%) | 34/53 | 62/132 (47.0%) | 94 | 70 | 24 | 74.5% [64.8–82.2] | n/a (WRONG > 0) |
| every eligible catcher (floor) | B | 94/117 (80.3%) | 46/63 | 63/119 (52.9%) | 79 | 56 | 23 | 70.9% [60.1–79.8] | n/a (WRONG > 0) |
| five shape checks that replicated (post-hoc, B-informed) | A | 46/105 (43.8%) | 14/53 | 5/132 (3.8%) | 186 | 127 | 59 | 68.3% [61.3–74.5] | n/a (WRONG > 0) |
| five shape checks that replicated (post-hoc, B-informed) | B | 51/117 (43.6%) | 22/63 | 3/119 (2.5%) | 182 | 116 | 66 | 63.7% [56.5–70.4] | n/a (WRONG > 0) |
| recommended six = five + empty result (post-hoc, B-informed) | A | 47/105 (44.8%) | 15/53 | 5/132 (3.8%) | 185 | 127 | 58 | 68.7% [61.6–74.9] | n/a (WRONG > 0) |
| recommended six = five + empty result (post-hoc, B-informed) | B | 52/117 (44.4%) | 23/63 | 3/119 (2.5%) | 181 | 116 | 65 | 64.1% [56.9–70.7] | n/a (WRONG > 0) |
| balanced + self-consistency re-sample (only where sampled; not chosen on A) | A | 66/105 (62.9%) | 23/53 | 13/132 (9.8%) | 158 | 119 | 39 | 75.3% [68.0–81.4] | n/a (WRONG > 0) |
| balanced + self-consistency re-sample (only where sampled; not chosen on A) | B | 67/117 (57.3%) | 32/63 | 22/119 (18.5%) | 147 | 97 | 50 | 66.0% [58.0–73.2] | n/a (WRONG > 0) |

Step by step, `balanced` (the `conservative` gate is the same list without steps 7 and 8):

| Step | Added catcher | A marginal WRONG / OK | A p-value | B marginal WRONG / OK | B cumulative: answered, WRONG, precision |
|---|---|---|---|---|---|
| 1 | shape_extra_cols (`ncols>1 & no and/comma/also`) | 29 / 4 | 0.0 | 31 / 3 | 202, 86, 57.4% |
| 2 | shape_single_measure (`percent & !grouped & !1x1`) | 6 / 0 | 0.0027 | 5 / 0 | 197, 81, 58.9% |
| 3 | single_row_plural (`plural-ask & nrows==1 & !aggword`) | 5 / 0 | 0.0055 | 3 / 0 | 194, 78, 59.8% |
| 4 | empty_or_null (`empty \| any NULL cell`) | 3 / 0 | 0.0382 | 3 / 3 | 188, 75, 60.1% |
| 5 | shape_list_measure (`list/which & !aggword & agg output`) | 4 / 1 | 0.0419 | 9 / 0 | 179, 66, 63.1% |
| 6 | evidence_columns (`identifier-like column missing`) | 4 / 1 | 0.0362 | 5 / 5 | 169, 61, 63.9% |
| 7 | shape_howmany (`howmany & !grouped & ncols>1`) | 2 / 0 | 0.09 | 3 / 0 | 166, 58, 65.1% |
| 8 | many_calls (`calls>=4`) | 3 / 1 | 0.0779 | 1 / 5 | 160, 57, 64.4% |
| 9 | sql_complexity (`joins>=3`) | 6 / 3 | 0.0184 | 2 / 2 | 156, 55, 64.7% |

What each catcher left out of the `conservative` gate would add on top of it:

| Catcher (A-chosen rule) | adds on A: WRONG / OK lost | adds on B: WRONG / OK lost |
|---|---|---|
| shape_howmany (`howmany & !grouped & ncols>1`) | 2 / 0 | 3 / 0 |
| retried (`attempts>1`) | 2 / 0 | 1 / 4 |
| many_calls (`calls>=4`) | 3 / 1 | 1 / 5 |
| topn_rowcount (`topN & nrows!=N`) | 0 / 0 | 1 / 0 |
| zero_or_negative (`single 0 \| negative`) | 1 / 1 | 2 / 1 |
| multi_row_singular (`the-superlative & nrows>1`) | 0 / 1 | 2 / 2 |
| truncated_reply (`finish=length`) | 2 / 2 | 1 / 3 |
| review_changed (`revised`) | 3 / 3 | 2 / 8 |
| percent_scale (`percent & (fraction or >100)`) | 2 / 3 | 1 / 3 |
| constant_column (`const col (>=2 rows)`) | 0 / 2 | 4 / 4 |
| slow (`latency>90s`) | 3 / 4 | 2 / 3 |
| doubt_in_reasoning (`mentions tie`) | 1 / 3 | 2 / 2 |
| duplicate_rows (`dup rows`) | 0 / 3 | 0 / 4 |
| rowcount_outlier (`nrows>200`) | 2 / 4 | 3 / 4 |
| limit_ties (`tie at any LIMIT \| reasoning mentions tie`) | 2 / 4 | 2 / 2 |
| identifier_output (`any id col & asks name/who`) | 5 / 6 | 7 / 2 |
| evidence_formula (`ev divide/percent & SQL no division`) | 4 / 6 | 6 / 7 |
| evidence_values (`compared number missing`) | 1 / 5 | 1 / 1 |
| join_without_fk (`not in JOIN KEYS list`) | 3 / 6 | 13 / 12 |
| limit_present (`any LIMIT`) | 4 / 9 | 7 / 6 |
| resampled (`resamples>0`) | 9 / 16 | 4 / 15 |

**Convention-tolerant view.** If a WRONG with the right values (the convention group of section 3) is counted as right, the same gates look like this. The last column is what a product user would lose: right values refused.

| Gate | Split | answered | right values (OK + convention WRONG) | precision on right values [95%] | right-value answers refused |
|---|---|---|---|---|---|
| none | A | 237 | 167 | 70.5% [64.4–75.9] | 0 |
| none | B | 236 | 158 | 67.0% [60.7–72.6] | 0 |
| replicated (post-hoc) | A | 186 | 139 | 74.7% [68.0–80.4] | 28 |
| replicated (post-hoc) | B | 182 | 129 | 70.9% [63.9–77.0] | 29 |
| recommended six (post-hoc) | A | 185 | 139 | 75.1% [68.4–80.8] | 28 |
| recommended six (post-hoc) | B | 181 | 129 | 71.3% [64.3–77.4] | 29 |
| conservative | A | 171 | 134 | 78.4% [71.6–83.9] | 33 |
| conservative | B | 165 | 121 | 73.3% [66.1–79.5] | 37 |
| balanced | A | 165 | 131 | 79.4% [72.6–84.9] | 36 |
| balanced | B | 156 | 113 | 72.4% [65.0–78.8] | 45 |

## 6. The 3/answered bound

WRONG does not reach 0 on B under any catcher or combination, so a rule-of-three bound (3/answered) applies nowhere and none is quoted.
- The lowest WRONG any offline gate reaches is the floor: 23 WRONG among 79 answered on B (17 truly wrong, 5 partial, 1 convention), after refusing 63 of 119 OK answers. A gate would need WRONG = 0 on at least 100 answered questions to get 3/answered down to 3%.
- The 17 truly-wrong answers that pass every check look clean. 14 of them are one row × one column (a count, percentage or date), and all had a single attempt and a confirmed review. They are wrong in what is counted (distinct entities versus rows), in which filter reading is used, or in which formula. No shape, process or schema check can see that; it needs a semantic check.
- With the self-consistency re-sample added (section 7), B still has 50 WRONG among 147 answered.

## 7. Self-consistency re-sample (new model calls)

**Plan, fixed before the first call** (`sc_plan.json`):
- One extra answer-phase call per question at temperature 0.7, through OpenVault with a strict pin on moonshotai/kimi-k3 and max_tokens 2048.
- The same frozen r2 prompts (prompt hash 4eb0d6754c7321da was checked) and the exact schema text the run used for that question.
- No review, no retry, no re-ask.
- The new SQL runs on the same mirror. Its row set is compared with the final answer's row set, ignoring column order, with floats rounded to 6 decimal places.
- Rule: refuse if the re-sample abstains or returns different rows. A variant that also refuses on an unusable or failed re-sample was measured too.

**Population and budget.**
- The population is the 321 answered questions that pass the frozen `balanced` gate, taken alternately from A and B in sha256('sc:'+key) order until the budget ran out.
- The budget was 285 chat requests, every 503 counted. It covered 86 A and 85 B questions, about half of each residual.
- Of the 285 requests, 168 were served (all nvidia:moonshotai/kimi-k3) and 117 were `503 pin_unavailable`.
- Tokens: 1,419,994 prompt and 60,835 completion.
- The key's ledger went from 1,642 to 1,927 requests for the day, which matches the count above.
- 16 re-samples were unusable replies (leaked template tokens and similar). The run would have re-asked those; this plan did not.

**Result.**
- **A sample (the choice).** 4/23 WRONG caught for 3/63 OK lost. That is below the 2:1 bar, so it is not added to the gate.
- **B sample.** 5/32 WRONG caught (15.6%, Wilson [6.9–31.8]) for 4/53 OK lost (7.5%).
- **Added to `balanced` on B** (applied only where B was sampled): answered 147, WRONG 50, precision 66.0% [58.0–73.2], against 64.7% without it. Scaled to the whole B residual it would be about 67.2%.
- **Why it catches so little.** 25 of the 32 sampled B wrong answers re-sample to exactly the same rows. The model is consistently wrong on them, and a second opinion from the same model cannot see that.
- Counting unusable or failed re-samples as disagreement is worse: 7 WRONG for 17 OK on B.

| Split | verdict of the final answer | agree | disagree | abstain | unusable | sql_fail | api_fail |
|---|---|---|---|---|---|---|---|
| A | OK | 52 | 3 | 0 | 4 | 2 | 2 |
| A | WRONG | 18 | 3 | 1 | 1 | 0 | 0 |
| B | OK | 36 | 4 | 0 | 10 | 3 | 0 |
| B | WRONG | 25 | 4 | 1 | 1 | 0 | 1 |

| Variant | Split sample | sampled answered | WRONG caught | OK lost | caught: convention / partial / truly wrong |
|---|---|---|---|---|---|
| disagree (abstain or different rows) | A | 86 | 4/23 (17.4%) | 3/63 (4.8%) | 1 / 2 / 1 |
| disagree (abstain or different rows) | B | 85 | 5/32 (15.6%) | 4/53 (7.5%) | 2 / 1 / 2 |
| disagree or no usable resample | A | 86 | 5/23 (21.7%) | 11/63 (17.5%) | 1 / 2 / 2 |
| disagree or no usable resample | B | 85 | 7/32 (21.9%) | 17/53 (32.1%) | 4 / 1 / 2 |

## 8. Caveats

- **Small A support.** Several chosen rules rest on 3–6 A cases. The A figures are in-sample and optimistic; only the B figures are estimates, and they carry the Wilson intervals shown.
- **Wording regexes are crude.** For example, `percent` also matches a filter such as "percent eligible > 0.1" in a list question. Those rules were measured as written, not tuned on B.
- **Grading is BIRD's set rule against SQLite gold**, with the mirror caveats from P1: 3 known mirror differences and 36 gold queries whose mirror fidelity is unmeasured.
- **The convention classes are a lower bound on 'right values'.** Matching is exact (1e-6) after column projection, plus key→name translation along lineage/FK; anything looser (a name built from two columns, a date format) stays in partial or truly wrong.
- **Shape checks enforce one output contract.** They pay off here largely because the frozen prompt's step 6 asks for a different contract from BIRD's gold (ranking measure and key included). Against an output contract that matches the prompt, the extra-column checks would catch less, and they also refuse right values: 26 of the 52 WRONG the six recommended checks catch on B are right values in the wrong shape.
- **Self-consistency calls.** 285 chat requests went through OpenVault, all counted against the 300 budget (503s included); 168 were served, all by nvidia:moonshotai/kimi-k3. The client's daily guard was set for this process only to the key's usage at launch plus 290 requests and 4,000,000 tokens (OV_DAILY_REQUEST_CAP=1932, OV_DAILY_TOKEN_CAP=13488143), so the guard itself enforced the budget. Without this the default guard (1,000 requests per UTC day) refuses every call, because the key had already made 1642 requests on 2026-10-09 during the r2 run.
- **Not tested.** A grain probe (re-run the final SQL with COUNT(x) ↔ COUNT(DISTINCT x) and refuse if the number moves) targets the largest truly-wrong group I saw in B. The idea came from reading B, so it would have to be chosen on A and confirmed on a fresh split.

## 9. Files

All under `scratchpad/p2/` unless noted.
- `common.py` (loaders, split, cell normalisation), `reexec.py` → `reexec.json` (full results + LIMIT-tie probe), `labels.py` → `wrong_classes.json` (gold-side WRONG classes; reporting only), `features.py` → `features.json` (gold-free signals).
- `catchers.py` (catalogue, choice on A, report on B) → `frozen_offline.json` (A-only choice and flag lists), `catchers_result.json`, `catchers_B.txt`; `extra_numbers.py` → `extra_numbers.json`, `marginal_on_top_conservative.json`.
- `sc_resample.py` (pre-registered plan in `sc_plan.json`, guard values in `sc_guard.json`) → `sc_resample.jsonl`, `sc_resample.log`; `sc_eval.py` → `sc_eval.json`.
- `tables.py`, `narrative.py`, `make_report.py` → `bird_work/P2_CATCHERS.md` (this file).
