# PLAN-SQL-VOTE-01: a second SQL reading as the serve check, measured (2026-10-18)

## Expected vs actual
- **Expected:** a SQL that passed every gate and ran with rows is right often enough to serve.
- **Actual:** on BIRD Mini-Dev split B through `POST /v1/contract/ask` (main 19d9bbf, `CORTEX_PLAN_SQL=1`, codestral-2508, no stronger route), 120 of the 286 served answers were wrong (58.0% precision). The gates prove a query is safe to run. Nothing checked that it was right.

## The signal
The SQL from earlier split-A runs (section 3 of `2026-10-18_engine-ai-path-measured.md`) was re-executed and compared in pairs:

| Pair | both served the same rows | right when they agree | right when they disagree |
|---|---|---|---|
| prompt fix x one-call #386 (same model) | 52 | 78.8% | 24.6% |
| prompt fix x direct v2 codestral | 53 | 79.2% | 25.8% |
| #386 x glm-5.3 (other model) | 63 | 84.1% | 22.2% |

**Correction, same day.** From 126 questions I saw B's SQL text equal A's on 11 and A right on 7 of them (64%), and I excluded identical text from the vote. At 500 questions that was wrong: the 49 identical-text agreements were right 41 times (84%), better than the 179 agreements between different SQL (75%). The exclusion cost 41 correct answers. See the follow-up below.

## What was built (`CORTEX_PLAN_SQL_VOTE=1`, default off)
The ladder's first SQL that runs with rows is a candidate:
1. **B:** the same model writes SQL for the question without the plan, through the same gates, with the ladder's error-fed retry (two writes). B's rows matching A's serve A, whether or not the SQL text is the same.
2. **C:** otherwise the stronger route OpenVault configured writes it. The candidate C matches is served.
3. **Split:** with no two agreeing, nothing is served and the ladder reconfirms (`PLAN_SQL_VOTE_SPLIT` on the envelope).

Rows compare as a set, numbers to four decimals, column names ignored. A first SQL written without a plan has no B: it would meet the same prompt and always agree. A model step in the vote with no OpenVault route stamp stops the answer, as anywhere on the ladder. `model_calls` keeps its keys. No contract change.

## Measured with the pair vote (no stronger route)
**Split B (held out), paired n=348:**

| Tree | OK | wrong | reconfirm or abstain | EX | answered precision | model calls per question |
|---|---|---|---|---|---|---|
| main 19d9bbf | 166 | 120 | 62 | 47.7% | 58.0% | 2.22 |
| pair vote | 99 | 34 | 215 | 28.4% | 74.4% | 3.05 |

- Wrong answers removed 90, added 4 (sign test p = 3e-22).
- Correct answers lost 73, gained 6 (p = 1e-15). B disagreeing with a right A is the cost.

**Split A, paired n=150:**

| Tree | OK | wrong | reconfirm or abstain | EX | answered precision |
|---|---|---|---|---|---|
| main 19d9bbf | 61 | 67 | 22 | 40.7% | 47.7% |
| pair vote | 38 | 12 | 100 | 25.3% | 76.0% |

Wrong answers removed 55, added 0 (p = 6e-17). Correct answers lost 27, gained 4 (p = 3e-5).

**Tie-break (stronger route) on live data:** Gemini (gemini-3.8-flash) served as the stronger route for 23 calls before its quota returned 429 on every call. The vote resolved 6 questions through it: 5 right and 1 wrong. That is too few to claim anything. Replaying the earlier runs (A = Cortex, B = direct codestral, C = glm-5.3), the full vote scored 80 OK, 22 wrong and 48 reconfirm on split A (EX 53.3%, precision 78.4%). That is a proxy, not the engine.

## Follow-up: why correct answers fell, and what recovered them
I recorded every executed candidate per question (the first SQL A, the second B, and failed attempts) for all 500 questions on main 56f1f7c, with the same setup as above, and scored each against gold.

**Where the correct answers went.** 221 first readings were right and the vote served 141 of them. Pairing the first two executed candidates per question (within 9 of the engine's own verdicts), the losses fall in four groups:

| Why a right first reading was not served | Questions |
|---|---|
| A and B wrote the same SQL text, and the vote refused to count that (8 wrong ones were also held back) | 41 |
| B failed: refused before running (10), failed EXPLAIN (7), or returned no rows (4); it had one write and no retry | 21 |
| B ran and returned other rows | 23 |
| A and B were each right but returned different rows | 4 |

**Fix 1: identical SQL counts.** **Fix 2: B gets the ladder's error-fed retry.** Both are in `plan_sql_ask.py`. Paired against main, dev split A first, then held-out split B:

| Split | Tree | OK | wrong | abstain | EX | answered precision |
|---|---|---|---|---|---|---|
| A (150) | pair vote, rerun on 56f1f7c | 41 | 15 | 94 | 27.3% | 73.2% |
| A (150) | fixes 1+2 | 55 | 15 | 80 | 36.7% | 78.6% |
| B (348) | pair vote, rerun on 56f1f7c | 98 | 30 | 220 | 28.2% | 76.6% |
| B (348) | fixes 1+2 | 145 | 43 | 160 | 41.7% | 77.1% |

On B the fixes gained 56 correct answers and lost 9 (p = 2e-9), and added 21 wrong answers while removing 8. Precision holds; the vote now keeps 145 of the 166 correct answers the single-shot path gives.

**Tried and not shipped** (each against the fixes 1+2 tree):
- **Stored-value hint** (feed back what a column holds when a filter matches nothing or counts zero). A: gained 7, lost 7. B: gained 12, lost 26 (p = 0.034); wrong fell 43 to 35. It turned wrong answers into abstains and did not turn them into right ones. #89 and #93 (`'East Bohemia'` stored as `'east Bohemia'`) went from wrong to abstain, not to right.
- **Header of the sampled-values block** ("ground filters in these" to "take the value from the question"). B: gained 11, lost 16 (p = 0.44). Noise.
- **Three SQL attempts instead of two.** First 71 questions of A: 22 correct against 24. Stopped.
- **AI judge on disagreements, same model** (shown both SQL and result previews; offline on A). It picks the right SQL in 25 of the 31 cases where exactly one is right, but it never rejects both: when neither is right it still picks one. Served wrong answers rose from 17 to 43 and precision fell to 61%. A variant that reasons first rejected more, at 66%.
- **Plurality over seven readings** (A, B and five temperature-0.7 samples). EX reached 50% on A at 58% precision. Where A and B disagreed, even six of seven readings agreeing was right only 59 to 63% of the time: the wrong answers are consistent misreadings, so more samples from the same model do not separate them.
- **Gemini as a verifier of the disputed groups** was not measured: its daily quota ran out.

**What this says.** The recoverable losses came from the vote's own design. The rest (a right reading exists for 64% of questions on A, and the vote serves 37%) is a selection problem the same model cannot solve. A stronger model breaking the ties is measured in the next section.

## The stronger-route tie-break, measured (2026-10-19)
When A and B disagree, the vote asks the stronger route OpenVault configured for a third SQL and serves the first or second reading it matches. Until now no stronger model was reachable. Today NVIDIA glm-5.3 was, so the local OpenVault stand-in published `routes.stronger = z-ai/glm-5.3` (codestral-2508 stayed the first model). Everything else is the same as above, run on main dd891d2. Each split ran once, paired against the same tree with no stronger route.

| Split | Tree | OK | wrong | abstain | EX | answered precision | model calls per question |
|---|---|---|---|---|---|---|---|
| A (150, dev) | vote, no stronger route | 55 | 15 | 80 | 36.7% | 78.6% | 3.11 |
| A (150, dev) | vote + glm-5.3 tie-break | 74 | 26 | 50 | 49.3% | 74.0% | 4.06 |
| B (348, held out) | vote, no stronger route | 145 | 43 | 160 | 41.7% | 77.1% | 3.08 |
| B (348, held out) | vote + glm-5.3 tie-break | 175 | 47 | 126 | 50.3% | 78.8% | 4.15 |

On B the tie-break gained 38 correct answers and lost 8 (p = 9e-6), and added 14 wrong answers while removing 10. Against the single-shot path on B (166 right, 120 wrong, 2.22 calls per question), the vote with a tie-break serves 9 more right answers and 73 fewer wrong ones, at about twice the model calls.

**How each served answer was reached** (right / wrong):

| Path | A | B |
|---|---|---|
| A and B agree | 54 / 20 (73%) | 144 / 34 (81%) |
| the stronger SQL matched the first reading | 10 / 1 | 14 / 8 |
| the stronger SQL matched the second reading | 10 / 5 | 17 / 5 |

34 of the 47 wrong answers on B come from two readings that agree on a consistent misreading. A third reading cannot catch those, so they are where the remaining wrong answers are.

**Caveats.**
- glm-5.3 is a stand-in for the production tier, which OpenVault configures. Its calls took 84 seconds on average through the stand-in.
- 24 questions on B (10 on A) lost their stronger call to a failure, probably the generator's 45-second timeout, so the tie-break is if anything understated.
- Gemini was tried first. The group verifier ran on A (52 right / 16 wrong with the vote alone, 67 / 22 with the verifier added: EX 44.7%, precision 75.3%) before the day's quota ran out. Its NONE count may include a few calls lost to that quota, so it is a partial result. The Gemini engine run got 429 on every call and was discarded.

## Repro
1. Copy each BIRD Mini-Dev SQLite database into DuckDB (DuckDB's sqlite extension).
2. For each database, set up three things:
   - a semantic layer listing its tables;
   - a signed grant over all of them;
   - `dms_payload` with BIRD's column descriptions, and its foreign keys as joins.
3. Ask each question, with BIRD's evidence appended, through `POST /v1/contract/ask`:
   - `CORTEX_PLAN_SQL=1`, plus `CORTEX_PLAN_SQL_VOTE=1` for the vote;
   - FreeRoute pointed at a loopback OpenVault stand-in that serves codestral-2508.
4. Score the served rows as a set against the SQLite gold rows.

The harness, the stand-in and the provider keys stayed in scratch. Engine code reached the model only through FreeRoute.

## Root-cause class
Safety gates stood in for correctness gates. The gates answer whether a query may run; nothing answered whether it is the query the question asked for.

## Invariants
- "A wrong answer is never served" (WRONG=0). The vote moves toward it. 34 wrong at n=348 is not zero, so neither setting should serve customers yet.
- "Don't claim 0 wrong below n=300."
- "Silent fallback is a lie." Every vote outcome is stamped on the envelope:
  - a split carries `PLAN_SQL_VOTE_SPLIT`;
  - an agreement says whether the two SQL texts were the same;
  - a first SQL written without a plan says it has no second reading;
  - a missing stronger route is stamped on its step.
- "Assert the artifact the customer receives." Every count above comes from the envelope's rows.

## What follows
1. **Choose what a wrong answer costs.** The tie-break lifts EX above the single-shot path with far fewer wrong answers, but 47 wrong in 348 is not zero and most come from agreed pairs. Serving an agreed pair only when a third reading also matches would cut them, at a cost in correct answers; that is a separate measurement.
2. **After #386 lands,** the first SQL is written without a plan, so the vote skips B and relies on the stronger route alone. B needs a framing of its own (a different instruction, or temperature above 0) to be a second reading again. Measure that before enabling the vote on top of #386.
3. **Keep `CORTEX_PLAN_SQL` off in production** on either setting until a held-out pack of 300+ shows WRONG near 0.

## Verified vs assumed
- **Verified:**
  - every count, from this session's runs;
  - the sign tests, from the paired verdicts;
  - the vote's behaviour, from `tests/test_plan_sql/test_plan_sql_vote.py`. 7 of its 9 tests fail with the vote forced off.
- **Assumed:**
  - that the tie-break recovers recall as the proxy shows;
  - that codestral behaves like the production OpenVault tier;
  - that BIRD's gains carry over to DMS questions.
