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

In the first vote run, B's SQL text equalled A's on 11 of 126 questions. A was right on 7 of those 11 (64%), below the 79 to 84% of two different queries that return the same rows. So identical text is weaker evidence and is not counted.

## What was built (`CORTEX_PLAN_SQL_VOTE=1`, default off)
The ladder's first SQL that runs with rows is a candidate:
1. **B:** the same model writes SQL for the question without the plan, through the same gates. B's rows matching A's serve A.
2. **C:** otherwise the stronger route OpenVault configured writes it. The candidate C matches is served.
3. **Split:** with no two agreeing, nothing is served and the ladder reconfirms (`PLAN_SQL_VOTE_SPLIT` on the envelope).

Rows compare as a set, numbers to four decimals, column names ignored. A model step in the vote with no OpenVault route stamp stops the answer, as anywhere on the ladder. `model_calls` keeps its keys. No contract change.

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
  - a repeated SQL is stamped "not counted";
  - a missing stronger route is stamped on its step.
- "Assert the artifact the customer receives." Every count above comes from the envelope's rows.

## What follows
1. **Measure the tie-break with a working stronger route.** During this session the candidates were all blocked:
   - Gemini's quota ran out after 23 calls;
   - NVIDIA glm-5.3 and mistral-medium returned 429;
   - mistral-large returned 403.
2. **After #386 lands,** the first SQL has no plan, so B's prompt equals A's. The repeated-SQL rule then leaves the decision to C, and B needs a framing of its own to stay a second reading.
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
