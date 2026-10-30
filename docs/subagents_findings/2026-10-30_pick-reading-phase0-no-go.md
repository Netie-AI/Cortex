# PICK-READING-01 Phase 0: NO-GO on split A (2026-10-30)

`CORTEX_PLAN_SQL_READINGS` stays off. #453 merged the feature default-off. The
plan's Phase 0 go/no-go (section 8.2) measured it on main and three of its four
bars fail. Per the plan, NO-GO means this finding, no flag-on and no Phase 2.

## Expected vs actual
- **Expected:** a vote split's reconfirm offers its disagreeing readings often
  enough, and the right one among them often enough, that a user who picks
  one gets a right answer without a wrong pick.
- **Actual:**
  - describe's core grammar refuses at least one reading in 70% of splits, so
    those splits get no offer.
  - 9 offers held 6 right readings.
  - A blind picker made 2 wrong picks out of 5, on faithful statements.

## Run
- Main `2c7e7e7` (#453, contract 1.10.0) with
  `CORTEX_PLAN_SQL=1 CORTEX_PLAN_SQL_VOTE=1 CORTEX_PLAN_SQL_READINGS=1`.
- BIRD Mini-Dev split A, n=150, through `POST /v1/contract/ask` in-process.
- duckdb **1.5.5** on all 150 records. A first launch on 2.0.0 was voided,
  because of the dropped-LIMIT bug that #455 fixed.
- Models:
  - writer: codestral-2508;
  - stronger route: `mistral-large-4-1t-a52b` on NVIDIA, 180 s timeout;
  - independent reviewer: `nvidia/nemotron-3-ultra-550b-a55b`, a family used
    nowhere else in the run;
  - blind picker: `gemini-3.5-flash`. It sees the question, the reconfirm
    text, the statements and the row counts, and never sees SQL or values.
    It may answer NONE.
- Every offered reading was picked through `plan_sql_reading_id` in the same
  process, inside the TTL.

Counts: 150 questions, 40 vote splits, 9 offers (8 with 2 readings, 1 with
3), 19 offered readings. Splits without an offer:
- 28 `offered=0 reason=describe-failed`;
- 0 collisions;
- 3 with fewer than 2 gated readings. In each of these the stronger route
  timed out, which happened on 46 of 150 asks.

| # | GO bar (redact-all, as shipped) | Value | Threshold | Result |
|---|---|---|---|---|
| 1 | Offers holding a reading labelled right | 6 of 9 (5 by the reviewer) | at least 10 | FAIL |
| 2 | Fidelity errors | 0 / 19 (3/n bound 15.8%, not proven) | 0 | PASS at n=19 |
| 3 | Blind picker wrong picks | 2 of 5 picks (3 right; NONE on 4 of 9 offers) | 0 | FAIL |
| 4 | Collision + describe-failure suppression | 28 / 40 = 70% | at most 20% | FAIL |

The `literal_in_question` predicate (plan 4c), rendered offline, does not
help: 29 of 40 splits are suppressed (72.5%), with 0 fidelity errors and 0
value hits.

## Root-cause class
**A safe-but-narrow describer gates the whole offer.**
- **Refusals per reading.** describe refuses anything outside its core
  grammar (the fail-closed design that took the misdescription fuzz to 0).
  First refusals per suppressed split:
  - 13 computed or ratio outputs;
  - 4 `IN (subquery)`;
  - 4 over-long statements;
  - 4 values it does not describe;
  - 2 scalar subqueries;
  - 1 computed sort key.
- **One refusal sinks the split.** A single refused reading suppresses the
  whole offer, and BIRD questions are mostly ratios and nested filters.
- **Faithful statements without values still mislead.** Both wrong picks had
  faithful statements:
  - **qid 344:** names vs ids. Both readings were wrong, so NONE was the right
    answer.
  - **qid 347:** the statement accurately says "keeping rows of cards with no
    match" (a LEFT JOIN), and the picker chose it over the right INNER JOIN.

  A description with no values cannot tell a user which reading the question
  means.

## Invariant
- **WRONG=0:** a wrong pick serves a wrong answer under the user's choice,
  which is still a wrong answer.
- **Assert the artifact the customer receives:** the pick envelopes were
  checked. All 19 returned 200 with badge `session`, null `model_calls`, a
  drillthrough token and an audit id, and none leaked a value.
- **Prove a gate can fail before trusting it green:** bars 1, 3 and 4 failed
  on real data, and that failure is the evidence.

## Also found (Phase 2 bar B6, not a Phase 0 bar)
qid 327: a pick of `SELECT DISTINCT … LIMIT 50` with no ORDER BY served a
different 50-row page than the offer showed. Gold has 186 rows, so the page
is not reproducible. Any later attempt must order a held reading's page
deterministically before offering it.

## What would move it (not started)
1. Offer the describable readings of a split instead of suppressing all of
   them. This needs a ruling: with fewer readings shown, the right one may be
   the hidden one.
2. Widen the grammar to ratio outputs and `IN (subquery)`, with the same
   DuckDB read-back and fuzz proof that holds misdescriptions at 0.
3. A disambiguating statement cannot show values, so the readings have to
   differ in words a user can judge. That needs a design, not a tuning pass.

## Repro
All of this is in the scratch harness, not committed:
- harness: `phase0_bank.py`, `runphase0.sh`, `label.py`, `metrics.py`;
- labels: `phase0_labels.A.final.jsonl`;
- metrics: `phase0_metrics.A.json`.

Pending Lead adjudication (section 8.5): reviewer-vs-gold disagreements on
327/1, 327/2 and 195/2. None of them changes a bar's result.
