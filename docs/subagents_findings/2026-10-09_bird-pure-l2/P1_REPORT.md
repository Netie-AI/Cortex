# P1 result: BIRD Mini-Dev, frozen pure-L2 harness (round r2)

Run `bird_minidev_r2`, 2026-10-08T20:18:51Z to 2026-10-09T07:18:28Z. Written 2026-10-09.
Model: `moonshotai/kimi-k3`, strictly pinned through the local OpenVault and served by NVIDIA on every call.
Benchmark: BIRD Mini-Dev (SQLite), 500 entries minus 2 identical duplicates (question_id 137 and 138), so n = 498.

## 1. Headline

- **Accuracy is 251 of 498 (50.4%, Wilson 95% 46.0–54.8%)** under BIRD's set rule.
- **Precision is 251 of 473 answered (53.1%, 48.6–57.5%).**
- The model answered 473 questions, abstained on 15 and errored on 10. All 10 errors are in `european_football_2`.
- No question is excluded. API_REFUSED is 0 in the final file. Nine questions were refused by the route on an earlier attempt and then re-run; they are listed in section 4.
- The stricter rules give 241 (multiset) and 246 (literal BIRD set).
- WRONG is above 0 in every cell of every table, so the rule-of-three bound (3/answered) applies nowhere.
- The adapter's `summary.json` has a `rule_of_three_95` field set to 3/n for every group. It is not a bound on anything observed in this run and should not be quoted.
- This is not a like-for-like comparison with the 52-question DMS pack held-out (17 of 22 non-trap questions OK, 20 of 26 correct including traps). The databases, the question style and the tuning exposure all differ. It is also not comparable with the old DMS path's 0 of 500 answered. That run never reached SQL generation, so it measured plumbing, not the model. Section 8 has both comparisons.

**Counting.** I took the latest verdict per question key in `runs/bird_minidev_r2.jsonl`. The file has 498 lines, 498 unique keys and 0 unparseable lines, and the keys equal the 498 in `questions.json`.

**Two independent checks agree exactly.** I recomputed every number in this report independently with `p1/p1_compute.py`. I also regenerated the summary with the adapter's own `summarise()` code (`p1/regen_summary.py`), because the adapter has no summarize subcommand. The two agree on every count and interval, and they match the adapter's end-of-run line: `set rule: OK 251/498 wrong 222 abstain 15 error 10 | multiset OK 241 | literal BIRD OK 246 | excluded {}`.

**Definitions.**
- answered = OK + WRONG.
- accuracy = OK / n.
- precision = OK / answered.
- Intervals are Wilson 95%.

**The three rules.**
- **BIRD set rule:** the sets of result tuples are equal, with numbers equal within 1e-6 relative. The tolerance exists because the answer runs in DuckDB and the gold query in SQLite.
- **Multiset rule:** the same tuples with the same multiplicities, with the same tolerance.
- **Literal BIRD set:** `set(pred) == set(gold)` with no tolerance, exactly as BIRD's `evaluation_ex.calculate_ex` does it.

ABSTAIN and ERROR are identical under all three rules; only OK and WRONG move.
- 10 questions are OK under the set rule and WRONG under the multiset rule, because duplicate counts differ: bird_0149, 0213, 0397, 0528, 0758, 0797, 0972, 1036, 1411, 1435.
- 5 questions are OK only because of the 1e-6 tolerance: bird_0169, 1195, 1390, 1473, 1476.
- No question is OK under a stricter rule and WRONG under a looser one.

## 2. Results by rule, difficulty and database

### Overall

| Rule | n | OK | WRONG | ABSTAIN | ERROR | API_REFUSED | answered | accuracy OK/n [Wilson 95%] | precision OK/answered [Wilson 95%] | 3/answered |
|---|---|---|---|---|---|---|---|---|---|---|
| BIRD set rule (1e-6 tolerance) | 498 | 251 | 222 | 15 | 10 | 0 | 473 | 251/498 = 50.4% [46.0–54.8] | 251/473 = 53.1% [48.6–57.5] | n/a (WRONG > 0) |
| Multiset rule | 498 | 241 | 232 | 15 | 10 | 0 | 473 | 241/498 = 48.4% [44.0–52.8] | 241/473 = 50.9% [46.5–55.4] | n/a (WRONG > 0) |
| Literal BIRD set (no tolerance) | 498 | 246 | 227 | 15 | 10 | 0 | 473 | 246/498 = 49.4% [45.0–53.8] | 246/473 = 52.0% [47.5–56.5] | n/a (WRONG > 0) |

### BIRD set rule (1e-6 tolerance): by difficulty

| Difficulty | n | OK | WRONG | ABSTAIN | ERROR | API_REFUSED | answered | accuracy OK/n [Wilson 95%] | precision OK/answered [Wilson 95%] | 3/answered |
|---|---|---|---|---|---|---|---|---|---|---|
| simple | 148 | 92 | 49 | 4 | 3 | 0 | 141 | 92/148 = 62.2% [54.1–69.6] | 92/141 = 65.2% [57.1–72.6] | n/a (WRONG > 0) |
| moderate | 248 | 110 | 122 | 10 | 6 | 0 | 232 | 110/248 = 44.4% [38.3–50.6] | 110/232 = 47.4% [41.1–53.8] | n/a (WRONG > 0) |
| challenging | 102 | 49 | 51 | 1 | 1 | 0 | 100 | 49/102 = 48.0% [38.6–57.6] | 49/100 = 49.0% [39.4–58.7] | n/a (WRONG > 0) |

### Multiset rule: by difficulty

| Difficulty | n | OK | WRONG | ABSTAIN | ERROR | API_REFUSED | answered | accuracy OK/n [Wilson 95%] | precision OK/answered [Wilson 95%] | 3/answered |
|---|---|---|---|---|---|---|---|---|---|---|
| simple | 148 | 91 | 50 | 4 | 3 | 0 | 141 | 91/148 = 61.5% [53.4–68.9] | 91/141 = 64.5% [56.4–72.0] | n/a (WRONG > 0) |
| moderate | 248 | 104 | 128 | 10 | 6 | 0 | 232 | 104/248 = 41.9% [36.0–48.1] | 104/232 = 44.8% [38.6–51.3] | n/a (WRONG > 0) |
| challenging | 102 | 46 | 54 | 1 | 1 | 0 | 100 | 46/102 = 45.1% [35.8–54.8] | 46/100 = 46.0% [36.6–55.7] | n/a (WRONG > 0) |

### Literal BIRD set (no tolerance): by difficulty

| Difficulty | n | OK | WRONG | ABSTAIN | ERROR | API_REFUSED | answered | accuracy OK/n [Wilson 95%] | precision OK/answered [Wilson 95%] | 3/answered |
|---|---|---|---|---|---|---|---|---|---|---|
| simple | 148 | 92 | 49 | 4 | 3 | 0 | 141 | 92/148 = 62.2% [54.1–69.6] | 92/141 = 65.2% [57.1–72.6] | n/a (WRONG > 0) |
| moderate | 248 | 107 | 125 | 10 | 6 | 0 | 232 | 107/248 = 43.1% [37.1–49.4] | 107/232 = 46.1% [39.8–52.5] | n/a (WRONG > 0) |
| challenging | 102 | 47 | 53 | 1 | 1 | 0 | 100 | 47/102 = 46.1% [36.7–55.7] | 47/100 = 47.0% [37.5–56.7] | n/a (WRONG > 0) |

### BIRD set rule (1e-6 tolerance): by database

| Database | n | OK | WRONG | ABSTAIN | ERROR | API_REFUSED | answered | accuracy OK/n [Wilson 95%] | precision OK/answered [Wilson 95%] | 3/answered |
|---|---|---|---|---|---|---|---|---|---|---|
| california_schools | 30 | 11 | 19 | 0 | 0 | 0 | 30 | 11/30 = 36.7% [21.9–54.5] | 11/30 = 36.7% [21.9–54.5] | n/a (WRONG > 0) |
| card_games | 52 | 29 | 21 | 2 | 0 | 0 | 50 | 29/52 = 55.8% [42.3–68.4] | 29/50 = 58.0% [44.2–70.6] | n/a (WRONG > 0) |
| codebase_community | 49 | 25 | 23 | 1 | 0 | 0 | 48 | 25/49 = 51.0% [37.5–64.4] | 25/48 = 52.1% [38.3–65.5] | n/a (WRONG > 0) |
| debit_card_specializing | 30 | 13 | 14 | 3 | 0 | 0 | 27 | 13/30 = 43.3% [27.4–60.8] | 13/27 = 48.1% [30.7–66.0] | n/a (WRONG > 0) |
| european_football_2 | 51 | 18 | 23 | 0 | 10 | 0 | 41 | 18/51 = 35.3% [23.6–49.0] | 18/41 = 43.9% [29.9–59.0] | n/a (WRONG > 0) |
| financial | 30 | 13 | 12 | 5 | 0 | 0 | 25 | 13/30 = 43.3% [27.4–60.8] | 13/25 = 52.0% [33.5–70.0] | n/a (WRONG > 0) |
| formula_1 | 66 | 29 | 36 | 1 | 0 | 0 | 65 | 29/66 = 43.9% [32.6–55.9] | 29/65 = 44.6% [33.2–56.7] | n/a (WRONG > 0) |
| student_club | 48 | 32 | 14 | 2 | 0 | 0 | 46 | 32/48 = 66.7% [52.5–78.3] | 32/46 = 69.6% [55.2–80.9] | n/a (WRONG > 0) |
| superhero | 52 | 38 | 14 | 0 | 0 | 0 | 52 | 38/52 = 73.1% [59.8–83.2] | 38/52 = 73.1% [59.8–83.2] | n/a (WRONG > 0) |
| thrombosis_prediction | 50 | 23 | 26 | 1 | 0 | 0 | 49 | 23/50 = 46.0% [33.0–59.6] | 23/49 = 46.9% [33.7–60.6] | n/a (WRONG > 0) |
| toxicology | 40 | 20 | 20 | 0 | 0 | 0 | 40 | 20/40 = 50.0% [35.2–64.8] | 20/40 = 50.0% [35.2–64.8] | n/a (WRONG > 0) |

### Multiset rule: by database

| Database | n | OK | WRONG | ABSTAIN | ERROR | API_REFUSED | answered | accuracy OK/n [Wilson 95%] | precision OK/answered [Wilson 95%] | 3/answered |
|---|---|---|---|---|---|---|---|---|---|---|
| california_schools | 30 | 11 | 19 | 0 | 0 | 0 | 30 | 11/30 = 36.7% [21.9–54.5] | 11/30 = 36.7% [21.9–54.5] | n/a (WRONG > 0) |
| card_games | 52 | 27 | 23 | 2 | 0 | 0 | 50 | 27/52 = 51.9% [38.7–64.9] | 27/50 = 54.0% [40.4–67.0] | n/a (WRONG > 0) |
| codebase_community | 49 | 25 | 23 | 1 | 0 | 0 | 48 | 25/49 = 51.0% [37.5–64.4] | 25/48 = 52.1% [38.3–65.5] | n/a (WRONG > 0) |
| debit_card_specializing | 30 | 13 | 14 | 3 | 0 | 0 | 27 | 13/30 = 43.3% [27.4–60.8] | 13/27 = 48.1% [30.7–66.0] | n/a (WRONG > 0) |
| european_football_2 | 51 | 17 | 24 | 0 | 10 | 0 | 41 | 17/51 = 33.3% [22.0–47.0] | 17/41 = 41.5% [27.8–56.6] | n/a (WRONG > 0) |
| financial | 30 | 12 | 13 | 5 | 0 | 0 | 25 | 12/30 = 40.0% [24.6–57.7] | 12/25 = 48.0% [30.0–66.5] | n/a (WRONG > 0) |
| formula_1 | 66 | 28 | 37 | 1 | 0 | 0 | 65 | 28/66 = 42.4% [31.2–54.4] | 28/65 = 43.1% [31.8–55.2] | n/a (WRONG > 0) |
| student_club | 48 | 30 | 16 | 2 | 0 | 0 | 46 | 30/48 = 62.5% [48.4–74.8] | 30/46 = 65.2% [50.8–77.3] | n/a (WRONG > 0) |
| superhero | 52 | 36 | 16 | 0 | 0 | 0 | 52 | 36/52 = 69.2% [55.7–80.1] | 36/52 = 69.2% [55.7–80.1] | n/a (WRONG > 0) |
| thrombosis_prediction | 50 | 23 | 26 | 1 | 0 | 0 | 49 | 23/50 = 46.0% [33.0–59.6] | 23/49 = 46.9% [33.7–60.6] | n/a (WRONG > 0) |
| toxicology | 40 | 19 | 21 | 0 | 0 | 0 | 40 | 19/40 = 47.5% [32.9–62.5] | 19/40 = 47.5% [32.9–62.5] | n/a (WRONG > 0) |

### Literal BIRD set (no tolerance): by database

| Database | n | OK | WRONG | ABSTAIN | ERROR | API_REFUSED | answered | accuracy OK/n [Wilson 95%] | precision OK/answered [Wilson 95%] | 3/answered |
|---|---|---|---|---|---|---|---|---|---|---|
| california_schools | 30 | 11 | 19 | 0 | 0 | 0 | 30 | 11/30 = 36.7% [21.9–54.5] | 11/30 = 36.7% [21.9–54.5] | n/a (WRONG > 0) |
| card_games | 52 | 29 | 21 | 2 | 0 | 0 | 50 | 29/52 = 55.8% [42.3–68.4] | 29/50 = 58.0% [44.2–70.6] | n/a (WRONG > 0) |
| codebase_community | 49 | 25 | 23 | 1 | 0 | 0 | 48 | 25/49 = 51.0% [37.5–64.4] | 25/48 = 52.1% [38.3–65.5] | n/a (WRONG > 0) |
| debit_card_specializing | 30 | 11 | 16 | 3 | 0 | 0 | 27 | 11/30 = 36.7% [21.9–54.5] | 11/27 = 40.7% [24.5–59.3] | n/a (WRONG > 0) |
| european_football_2 | 51 | 18 | 23 | 0 | 10 | 0 | 41 | 18/51 = 35.3% [23.6–49.0] | 18/41 = 43.9% [29.9–59.0] | n/a (WRONG > 0) |
| financial | 30 | 12 | 13 | 5 | 0 | 0 | 25 | 12/30 = 40.0% [24.6–57.7] | 12/25 = 48.0% [30.0–66.5] | n/a (WRONG > 0) |
| formula_1 | 66 | 29 | 36 | 1 | 0 | 0 | 65 | 29/66 = 43.9% [32.6–55.9] | 29/65 = 44.6% [33.2–56.7] | n/a (WRONG > 0) |
| student_club | 48 | 31 | 15 | 2 | 0 | 0 | 46 | 31/48 = 64.6% [50.4–76.6] | 31/46 = 67.4% [53.0–79.1] | n/a (WRONG > 0) |
| superhero | 52 | 38 | 14 | 0 | 0 | 0 | 52 | 38/52 = 73.1% [59.8–83.2] | 38/52 = 73.1% [59.8–83.2] | n/a (WRONG > 0) |
| thrombosis_prediction | 50 | 22 | 27 | 1 | 0 | 0 | 49 | 22/50 = 44.0% [31.2–57.7] | 22/49 = 44.9% [31.9–58.7] | n/a (WRONG > 0) |
| toxicology | 40 | 20 | 20 | 0 | 0 | 0 | 40 | 20/40 = 50.0% [35.2–64.8] | 20/40 = 50.0% [35.2–64.8] | n/a (WRONG > 0) |

## 3. Diagnostics that bear on the numbers

- **The DuckDB mirror is a source of WRONG verdicts.**
  - The gold-check step ran a sqlglot SQLite-to-DuckDB translation of each gold query on the mirror and compared it with SQLite.
  - On 459 of 498 questions the mirror reproduces gold within 1e-6. Accuracy on those 459 is 240/459 = 52.3% (47.7–56.8%).
  - On 3 questions the mirror's result differs from gold (bird_0212, bird_1389, bird_0847). All 3 are WRONG here, and some or all of those WRONGs may be artifacts of the mirror.
  - 36 gold queries could not be translated, because they use SQLite-only constructs such as julianday, text-date arithmetic or bare GROUP BY columns. The mirror's fidelity on those is unmeasured. They score 11 OK, 24 WRONG and 1 ABSTAIN (30.6%, 18.0–46.9%), well below the rest. Some of that gap may come from the engine change rather than from the model.
- **The 2048-token clamp truncated replies.**
  - In the final traces, 21 of 1,097 served calls stopped with `finish_reason=length` at 2048 completion tokens.
  - They fall in 20 questions, which ended 5 OK, 11 WRONG, 3 ERROR and 1 ABSTAIN.
  - Ceiling on what the clamp could have cost: if all 15 non-OK questions would have been OK at 8192 tokens, accuracy would be 266/498 = 53.4%. This is an upper bound, not an estimate.
  - For scale, the frozen run on the DMS pack exceeded 2048 completion tokens on 1 of 106 calls.
- **All 10 ERRORs are in `european_football_2`, and every one is an unusable reply.**
  - Each ends with `format: unusable reply (leaked chat-template tokens)` after the frozen re-asks.
  - That database has by far the largest schema context: 88,250 characters, against 20,201 for the next largest.
  - Its ERROR rate is 10/51 = 19.6% (11.0–32.5%). No other database has an ERROR.
  - Across the run, 79 questions had at least one unusable reply that was re-asked. The replies broke down as 109 leaked chat-template tokens, 19 empty and 4 runaway repetition.
- **WRONG answers that contain the gold columns.** In 51 of the 222 WRONG answers, some choice of the answer's columns set-equals gold. In other words the model returned the right rows plus extra or reordered columns. BIRD scores these WRONG, and so does this report.
- **Latency per question:** median 29.8 s, p90 78.3 s, max 362.4 s. The per-question latencies add up to 5.49 h.

## 4. ABSTAIN, ERROR and API_REFUSED

**API_REFUSED (excluded):** none in the final file.

The adapter drops API_REFUSED rows on `--resume` and asks those questions again. Nine questions went through that.
- In every case the earlier attempt had **0 served calls**: each was a single call answered `503 pin_unavailable` ("pinned model has no healthy hop", with reason `parked` or `circuit_open`).
- So no model output was thrown away and nothing about the outcome was seen before the re-run.

| Key | Database | Final verdict |
|---|---|---|
| bird_0012 | california_schools | OK |
| bird_0046 | california_schools | WRONG |
| bird_0740 | superhero | OK |
| bird_0960 | formula_1 | OK |
| bird_0972 | formula_1 | OK |
| bird_1028 | european_football_2 | WRONG |
| bird_1139 | european_football_2 | ERROR |
| bird_1141 | european_football_2 | ERROR |
| bird_1147 | european_football_2 | WRONG |

**ERROR (10).** All are in european_football_2 and all are `format: unusable reply (leaked chat-template tokens)`:
- Simple (3): bird_1039, bird_1116, bird_1145.
- Moderate (6): bird_1057, bird_1098, bird_1102, bird_1110, bird_1141, bird_1148.
- Challenging (1): bird_1139.

**ABSTAIN (15).** BIRD has no unanswerable questions, so every abstention counts as a miss. Each line gives the model's stated reason, shortened.
- bird_1500 (debit_card_specializing, simple): "yearmonth has no product column".
- bird_0948 (formula_1, simple): "'points' is ambiguous between constructorResults and constructorStandings".
- bird_0095 (financial, moderate): "'youngest and highest average salary' has multiple readings".
- bird_0483 (card_games, moderate): "rulings has no language dimension".
- bird_0189 (financial, moderate): "'oldest' and 'lowest average salary' give no cutoff".
- bird_0094 (financial, challenging): "client-level age combined with district-level salary, ambiguous".
- bird_1524 (debit_card_specializing, simple): "no column stores customer nationality".
- bird_0402 (card_games, moderate): "question conflicts with the supplied formula".
- bird_1501 (debit_card_specializing, moderate): "June 2013 data only in yearmonth, which has no GasStationID".
- bird_0092 (financial, simple): "no salary by gender".
- bird_1275 (thrombosis_prediction, moderate): "evidence values '-', '+-' do not exist in the data".
- bird_1323 (student_club, moderate): "no way to identify fundraiser events".
- bird_0137 (financial, moderate): "no branch/location column matching 'Branch location 1'".
- bird_1387 (student_club, moderate): "no budget-manager role".
- bird_0685 (codebase_community, moderate): "'the user who posted it last time' is ambiguous".

## 5. Deviations from the frozen configuration

The frozen configuration is round r2 of the pure-L2 harness: `l2pure/run_r2.run.json`. Round 3 was rejected and `l2.py` was restored to `l2_r2.py`.

| # | Deviation | Frozen | This run | Effect seen in the data |
|---|---|---|---|---|
| 1 | OpenVault output-token clamp | `max_tokens` 8192 | `--max-tokens 2048`, which is OpenVault's server ceiling (`max_output_tokens_per_request` 2048 in `caps.json`). Recorded in `run.json` `deviations_from_frozen`. | 21 of 1,097 served calls in the final traces stopped on length, across 20 questions (section 3). The ledger shows 27 completions of exactly 2048 tokens in the run window; the other 6 were in attempts that were discarded. |
| 2 | Route: strict NVIDIA pin through OpenVault | Direct NVIDIA call (`provider: nvidia` in `l2pure/run_r2.run.json`) | OpenVault FreeRoute with `X-OpenVault-Strict: 1` on `moonshotai/kimi-k3` (`strict_pin: true`). There is no fallback model: OpenVault either serves the pin or returns 503. | Every row is served by `nvidia:moonshotai/kimi-k3` (498 of 498), and so is every successful call in the ledger (1,149 of 1,149). 680 of 1,829 requests in the run window (37.2%) were `503 pin_unavailable`. The frozen backoff (2/4/8/16 s, 5 tries) absorbed most of them; 9 questions were refused and re-run. There were 0 HTTP 429s. |
| 3 | DuckDB mirror | Answers run on the DMS warehouse (DuckDB) | BIRD's SQLite databases are mirrored into DuckDB. The model writes DuckDB SQL against the mirror, and the gold SQL runs in SQLite on a read-only copy. | Mirror fidelity is measured on 462 gold queries: 459 match within 1e-6 and 3 differ. 36 gold queries cannot be translated, so their fidelity is unmeasured (section 3). The plan records a column-level check of the mirror against SQLite (count, sum/min/max, text length) with 0 mismatches. |
| 4 | Eval key on the pro tier | Not applicable (direct NVIDIA) | The OpenVault eval key is tier `pro` (120 requests/min, 400,000 tokens/min). The plan (`bird_plan.json`) was drawn up against `free` (20/min, 40,000/min). | All 1,842 ledger rows for the key are tier `pro`. There were 0 rate-limit 429s. The tier changes speed only; the harness's 1.5 s pacing is unchanged. |
| 5 | Client daily guard raised | Not applicable | `ov_client.py` defaults to 1,000 requests and 3,000,000 tokens per UTC day. The run commands set `OV_DAILY_REQUEST_CAP=6000` and `OV_DAILY_TOKEN_CAP=15000000`. | On 2026-10-09 the key used 1,642 requests and 9,488,143 tokens, more than either default. With the default guard the run would have stopped partway through 2026-10-09. No `client_daily_cap` refusal appears in any row. |
| 6 | Run resumed after container restarts, in chunks | One continuous run | There were two container restarts (details in the notes below the table). Afterwards the run went in `--resume` chunks. The workflow's chunk command uses a 540 s timeout; the timeout for `r2_chunk2..5` is not recorded. Logs survive for 32 process starts: two in `bird_work/bird_minidev_r2.out`, plus `bird_work/r2_chunk2..5.log` and `chunk_r2_2..27.log`. | A question still in flight when a chunk was killed was discarded and re-asked from scratch: 52 served calls in the ledger (415,514 prompt and 29,470 completion tokens) have no counterpart in the final traces (1,149 against 1,097). The kill is time-based and happens before grading, so the outcome cannot bias it. The prompt changed in one line: rows 0–48 carry `CURRENT_DATE is 2026-10-08` and rows 49–497 carry `2026-10-09`. Across all 11 databases, that line is the only difference in schema context. |

Notes on row 6:
- **Restart 1, about 20:52:31Z.** The last ledger event and the last OpenVault log line are at 20:52:31. OpenVault PID 7381 stopped with no shutdown line, and the run stopped after 49 questions without writing its summary. OpenVault came back at 21:51:03 as PID 378, a low PID that indicates a fresh container. The ledger has no eval-key calls between 20:52:31 and 01:39.
- **Restart 2, before 01:38:00Z.** The kernel boot time is 2026-10-09T01:38:00Z, and OpenVault came back at 01:38:23 as PID 370. The run resumed at about 01:39.

Everything else matches the frozen values: model, retries 2, votes 1, resamples 2, review on (10 rows), retry_on_empty off, sql_timeout 10 s, min_gap 1.5 s, max_tries 5, timeout 240 s. The `l2.py` and prompt hashes are unchanged (section 6).

Changes that come with the benchmark rather than the harness:
- The question text is `question + "\n\nExternal knowledge: " + evidence`, which is BIRD's standard evidence setting.
- The scope is all tables of the question's database.
- Run order is sorted by `sha256('bird-minidev:<question_id>')`.

**Prep incident.** At 19:56:01Z, before the eval key existed, one live call went out during offline prep. It answered bird_1375 through OpenVault: 3 requests, 14,547 prompt and 369 completion tokens, and the answer was OK. The record is in `runs/ACCIDENTAL_LIVE_20261008T195601.*`. bird_1375 is the first question in r2 order. The plan says no tuning used it.

## 6. Configuration hashes

I recomputed each value from the files on disk on 2026-10-09 and compared it with `runs/bird_minidev_r2.run.json`. Every one matches. Values are sha256, truncated to 16 hex characters as the adapter records them.

| Item | Value | Matches |
|---|---|---|
| `l2pure/l2.py` (frozen harness; identical to `l2_r2.py`) | e92dc2c777ab03bd | run.json, frozen r2 |
| prompts (`l2.prompt_hash()` over `l2pure/prompts/*.txt`) | 4eb0d6754c7321da | run.json, frozen r2 |
| `bird_adapter.py` | 12bcee20489dda46 | run.json |
| `ov_client.py` | e9ec04d1e51e7f50 | run.json |
| `bird_work/gold_cache.jsonl` | 468cfe6f6e95b9b5 | run.json |
| BIRD source `mini_dev_sqlite.json` | 4ba5fa8de5585622 | questions.json |
| `bird_work/questions.json` | 7af1b3a84dacb7b7 | (recorded here) |
| DuckDB mirrors (11 files) | california_schools 0af3735edc1e6439, card_games cc3b0e879a4f8871, codebase_community 4c697277d1a9668e, debit_card_specializing a5256308dd0a4c10, european_football_2 74a3c8f0eb981be3, financial 6a0f889f0623884b, formula_1 f585aad0227b50e5, student_club a0c438d38dba7357, superhero 8d307dc86c224d18, thrombosis_prediction d8d4d6c6bf930a20, toxicology a9ce8af11e5b2ed9 | run.json and manifest.json, 11 of 11 |
| Result file `runs/bird_minidev_r2.jsonl` (as of this report) | ff3f125ddae4cc61 | (recorded here) |
| Trace file `runs/bird_minidev_r2.trace.jsonl` | 10a80d9ae1c7bafa | (recorded here) |

`frozen_match: true` in run.json.

## 7. OpenVault usage for the eval key

**Key.** key_id `d950a260214d`, label `claude-eval-harness`, tier `pro`, issued 2026-10-08T20:16:38Z. No key value is shown here.

**Source.** OpenVault `GET /api/usage`, called with the eval key itself, so the server scopes the result to that key. I paged by `since` because the endpoint returns at most 1,000 events per call. The paged events add up exactly to the server's own summary.

| Window | Requests | HTTP 200 | 503 pin_unavailable | Prompt tokens | Completion tokens | Total tokens |
|---|---|---|---|---|---|---|
| **Key lifetime (server summary)** | **1,842** | 1,160 | 682 (`failed_requests`) | **9,937,995** | **339,602** | **10,277,597** |
| Smoke run `bird_smoke` (5 questions, 20:16:38–20:18:50Z) | 13 | 11 | 2 | 56,485 | 2,072 | 58,557 |
| r2 run window (20:18:50Z – 07:18:31Z) | 1,829 | 1,149 | 680 | 9,881,510 | 337,530 | 10,219,040 |
| of which before restart 2 (to 01:38:00Z) | 187 | 114 | 73 | 696,872 | 34,025 | 730,897 |
| of which after restart 2 | 1,642 | 1,035 | 607 | 9,184,638 | 303,505 | 9,488,143 |
| UTC day 2026-10-08 (includes the smoke run) | 200 | 125 | 75 | 753,357 | 36,097 | 789,454 |
| UTC day 2026-10-09 | 1,642 | 1,035 | 607 | 9,184,638 | 303,505 | 9,488,143 |
| After the run (after 07:18:31Z) | 0 | – | – | 0 | 0 | 0 |

- **Ledger row properties.** `estimated_tokens` is 0, so all counts are provider-reported. Every requested model was `moonshotai/kimi-k3`, and every served model was `nvidia:moonshotai/kimi-k3`. The ledger has no 429 rows.
- **The ledger against the result rows.** The rows record 1,100 model calls, 1,097 of them with usage blocks, for 9,465,996 prompt and 308,060 completion tokens. The ledger has 52 more served calls in the run window, which are the discarded in-flight attempts from deviation 6.
- **Cost.** The ledger has `priced: false`, and its USD unit reads "NEEDS-YOU", so OpenVault records no dollar figure. `caps.json` says no paid key is pooled in this vault.

## 8. Comparisons

| Measurement | n | Answered | Correct | Accuracy [Wilson 95%] | Precision | Status |
|---|---|---|---|---|---|---|
| **This run: BIRD Mini-Dev, frozen r2 harness, set rule** | 498 | 473 | 251 OK | 50.4% [46.0–54.8] | 53.1% [48.6–57.5] | measured here |
| Old DMS path on BIRD Mini-Dev (DMS PR #312) | 500 | 0 | 0 | 0.0% [0.0–0.8] | undefined (0 answered) | quoted from the PR, **not re-run** |
| 52-question DMS pack, held-out half, r2 | 26 | 23 | 20 correct (17 OK plus 3 traps correctly refused) | 76.9% [58.0–89.0] | 17/23 = 73.9% [53.5–87.4] | from `l2pure/` files, not re-run |
| Same held-out half, non-trap questions only | 22 | 22 | 17 OK | 77.3% [56.6–89.9] | 17/22 = 77.3% | from `l2pure/run_r2.tally.json` |

**How to read these numbers.**
- **0 of 500 measured plumbing.** DMS PR #312 (merged 2026-10-05) reports `n=500 answered=0 RIGHT=0 ABSTAIN=500 WRONG=0`, and states "No provider served any question" and "Not a model-capability number". That run used the BIRD PostgreSQL dump through the full DMS-to-Cortex product path, and Cortex refused before generation (the PR attributes this to `InsightsAskIn` dropping the `ontology`/`mode` fields). Going from 0 to 473 answered shows that the pure-L2 harness reaches generation on these databases. It does not show a model getting better.
- **BIRD should be compared with the held-out non-trap figure.** BIRD has no trap (unanswerable) questions, so its accuracy belongs next to 17 of 22, not 20 of 26.
- **Even that comparison is loose.** The 52-question pack runs on the DMS warehouse schema, and the r2 prompts were tuned on the dev half of the same pack: 8 of the 26 held-out questions repeat dev wording. It also ran direct to NVIDIA with 8192 max tokens.
- **The held-out interval is wide.** At n = 22 it spans 56.6–89.9%.
- **The held-out figure also includes a re-run.** It is the value after the completion re-run (one held-out question was refused with 429 five times in the raw r2 run); the raw value was 19 of 26.
- **Different benchmarks.** BIRD's 50.4% [46.0–54.8] sits below the held-out interval. The gap reflects different databases and question styles, not a measured drop from one to the other.

## 9. Verified versus assumed

**Verified in this session**, by reading files and calling read-only endpoints:
- **Rows and counts.** Verdict counts are the latest per key over 498 unique keys, with 0 bad lines and 0 duplicate rows, and the keys match questions.json. The adapter's `summarise()` and an independent recomputation agree on every count and interval. The regenerated `summary.json` equals the chunk-27 summary apart from timing fields.
- **Hashes and file times.** All hashes in section 6 were recomputed and match `run.json` and the manifest. File times on the harness, adapter, client, prompts, mirrors and gold cache all predate the run start (latest is `bird_adapter.py` at 2026-10-08 19:57:30Z).
- **Route and model.** All 498 rows have route `openvault` and served model `nvidia:moonshotai/kimi-k3`. All 1,149 successful ledger calls served that model, and all ledger rows are tier `pro`.
- **The 2048 clamp is in effect.** The ledger maximum completion is 2048 and there are 21 length stops in the final traces. `caps.json` gives a ceiling of 2048.
- **Restarts.** Both container restarts are established by `ov_data/server.log` (PIDs 7381 → 378 → 370, no shutdown lines), the kernel boot time and the ledger gap.
- **Client guard.** The raised values (6,000 requests / 15,000,000 tokens) appear in the recorded run and resume commands. The ledger shows that 2026-10-09 usage exceeded both defaults with no client-side refusal.
- **Re-run questions.** All 9 earlier attempts had 0 served calls.
- **Schema context.** It differs only in the CURRENT_DATE line.
- **Old DMS path.** DMS PR #312's figures were read from the PR via the GitHub API.
- **52-pack figures.** These come from `l2pure/run_r2.tally.json` and `l2pure/REPORT.md`.

**Assumed or inferred, not verified:**
- **Earlier chunks used the same code.** `run.json` is rewritten on every resume, so it certifies only the last chunk (27). That earlier chunks ran the same adapter, harness, prompts and client is inferred from unchanged file times and the current hashes, not from records kept per chunk.
- **Some progress logs are missing.** None survives for questions 50–58 and 90–135, though their rows and traces are complete. That those chunks used the same command (guard raised, `--max-tokens 2048`, strict pin) is inferred from the ledger (tier, served model, no client-cap refusals, maximum 2048 completion tokens) and from the rows.
- **The 52 unrecorded calls.** That the 52 served calls missing from the rows are killed in-flight attempts is inferred from the counts; I did not trace them question by question.
- **Very long attempts would have been lost silently.** An attempt longer than one chunk (540 s in the workflow command) could never be recorded. The longest recorded question took 362 s, so I assume none was lost this way.
- **Frozen values not in the r2 record.** Some frozen values (review_rows 10, sql_timeout 10 s, vote_temperature 0.7, timeout 240 s) are not in `l2pure/run_r2.run.json`; they come from the adapter's `FROZEN` constant.
- **Cost.** A cost of $0 rests on `caps.json` ("no paid key is in this vault"). OpenVault does not price the usage, and I did not check provider consoles.
- **Training-data exposure.** I cannot tell whether the model saw BIRD Mini-Dev during training. BIRD is public, so contamination is possible.
- **Old DMS path not re-run.** The 0 of 500 was not re-run, and it measured a different pipeline on a different engine (PostgreSQL).
- **Held-out isolation.** "Held-out was never used for tuning" is a process claim from `l2pure/REPORT.md`; the files are consistent with it but cannot prove it.

## 10. Files

All paths are under `scratchpad/`.
- `bird_work/runs/bird_minidev_r2.jsonl`, `.trace.jsonl`, `.run.json`: the run itself, unchanged.
- `bird_work/runs/bird_minidev_r2.summary.json`: regenerated with the adapter's `summarise()` over the latest row per key.
- `bird_work/runs/bird_minidev_r2.summary.chunk27.json`: the previous summary, which the last chunk wrote.
- `p1/p1_compute.py` and `p1/p1_numbers.json`: the independent recomputation (full tables, lists and diagnostics).
- `p1/regen_summary.py`: the summary regeneration.
- `p1/ov_usage_agg.py`: the eval-key ledger aggregation (read-only; prints no key values).
