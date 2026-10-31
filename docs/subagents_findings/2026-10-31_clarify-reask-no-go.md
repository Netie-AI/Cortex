# CLARIFY-REASK: NO-GO on split A (2026-10-31)

Nothing was built in the engine. After PICK-READING-01 Phase 0 failed
(`2026-10-30_pick-reading-phase0-no-go.md`), the founder chose
clarify-and-re-ask: on a vote split, ask one clarifying question, then run
the user's reply through the whole ladder and vote, and serve nothing from a
pick. The hard bar was 0 added wrong answers against OFF on paired
questions. It fails on split A, so held-out split B was not run and no flag
or contract field exists.

## Expected vs actual
- **Expected:** the user's reply removes the ambiguity that split the vote.
  The refined ask, gated like any other ask, then serves right answers that
  OFF only reconfirmed, and adds no wrong ones.
- **Actual:** 12 clarifying questions were asked. The 12 refined asks gave
  3 right, **6 wrong** and 3 reconfirms. Paired on the same first ask, that
  is **+3 right and +6 wrong** with an optimistic simulated user, and
  +3 right and +5 wrong when "I can't tell" is a button.

## Run
- Main `9b1c4e1` (contract 1.11.0), duckdb **1.5.5**, BIRD Mini-Dev
  split A, n=150, through `POST /v1/contract/ask` in-process.
- Flags in both arms: `CORTEX_PLAN_SQL=1 CORTEX_PLAN_SQL_VOTE=1`. The
  CLARIFY arm adds a scratch hook in the final reconfirm branch of
  `CortexOS/dms/plan_sql_ask.py`, used only when `vote_split` is set.
- Models (the Mistral API returned HTTP 402, so this is a re-baseline and
  is not comparable to the 2026-10-18 numbers):
  - writer and clarifier: `openai/gpt-oss-20b` on NVIDIA. It was chosen
    over `minimax-m3.1-flash` on latency and rate-limit headroom, not on
    accuracy;
  - stronger route: `mistralai/mistral-large-4-1t-a52b` on NVIDIA. It was
    too slow to act as clarifier (73 to 357 s per call), and the two runs
    that tried it are void;
  - simulated user and gold-intent writer:
    `nvidia/nemotron-3-ultra-550b-a55b`, used nowhere else in the run.
- **The clarifying question.** One model call replaces the split's reconfirm
  call. It sees the schema, the question and the proven contrast facts
  between the readings: the PICK-READING v2 facts, cross-checked by DuckDB's
  own parser. It never sees SQL, values or rows. A deterministic check
  refuses an ASK that reads like SQL or that holds a literal or number the
  question does not hold. The prompt's list of common missing details comes
  from the 9 ambiguity patterns of the lane relabel.
- **The re-ask.** `original + " Clarification: " + reply`, run as a new ask
  through the whole ladder and vote, with clarify off for that ask.

| Arm (n=150) | Right (EX) | Wrong | Reconfirm | Model calls per question |
|---|---|---|---|---|
| OFF | 67 (44.7%) | 52 | 31 | 5.57 |
| CLARIFY run, first ask only | 70 (46.7%) | 46 | 34 | 5.83 |
| CLARIFY, optimistic user | 73 (48.7%) | 52 | 25 | 6.37 |
| CLARIFY, "can't tell" button | 73 (48.7%) | 51 | 26 | 6.33 |

Two runs of the same first-ask code flip 33 of 150 verdicts (provider
nondeterminism at temperature 0). The arm-level differences are therefore
inside run noise, and only the in-run pairing isolates clarify.

Clarify path: 24 vote splits, 12 questions asked. The other 12 were not asked:
- 7 had fewer than 2 readings with proven facts;
- 3 were refused by the check, 1 of them falsely (the word "join");
- 2 had readings that differ in no proven fact.

## The added wrong answers
| qid | What happened | Class |
|---|---|---|
| 881 | The reply chose the right reading. The refined SQL used integer `DIVIDE`, giving 0 against gold 17.24, and both readings agreed on it. | Right reply, wrong SQL agreed by the vote |
| 955 | The reply chose the right reading. The time-string parse was off from gold's. | Right reply, wrong SQL agreed by the vote |
| 1235 | The reply chose the right reading. The columns were out of gold's order and `DISTINCT` was missing. | Right reply; partly an EX artifact |
| 11 | The user's words "school code" matched the column `School Code`; gold returns `CDSCode`. | Reply mapped onto the wrong column |
| 1080 | The user chose distinct players; gold counts attribute rows. | Reply mapped onto the wrong column; arguably a gold artifact |
| 17 | A free-text "I didn't specify which…" reply was re-asked and served. Optimistic arm only. | Free-text non-answer served |

Even if 1080 and 1235 are ruled artifacts, at least 3 genuine wrong
answers remain (11, 881, 955), so the bar still fails.

The questions themselves were good. They were on target (892: which table's
points; 1136: distinct players or rows; 1068: which date), and none carried
a value: the check refused the two that did.

## Root-cause class
**A refined ask removes the only check that was holding a hard question
back.** On these questions the vote's disagreement was what kept a wrong
answer from being served. Once the reply settles the wording, both writers
read it the same way and agree on the same systematic error. The refined
ask is "gated like any other ask", and on this writer that serves 52 wrong
answers out of 119 served (precision 56%). Converting reconfirms into
ordinary asks inherits that precision, and a value-free question cannot fix
either an SQL error or a user's mapping of their own words onto the wrong
column.

## Invariant
- **WRONG=0:** a wrong answer served on the user's clarified question is
  still a wrong answer.
- **Prove a gate can fail before trusting it green:** the bar failed on real
  data, and that failure is the evidence.
- **Silent fallback is a lie:** "I can't tell" has to be a structured button
  that falls to the closest-question reconfirm. Free text must never be read
  as "I don't know" (qid 17). A classifier that did read it that way would be
  a word rule.

## What would move it (not started, not measured)
1. **Bind the refined answer to a proven reading.** Serve only when the
   refined vote's rows equal those of one of the original, already-gated
   split readings, and the clarified fact matches that reading's contrast
   fact. That would have blocked 881, 955 and 1235. It is picking again,
   though, and Phase 0 showed that picking fails when no reading is right.
2. **A stronger writer.** It raises the base precision, not the mechanism's.
   The best measured writer (codestral) is blocked by the 402.
3. If it is ever built: flag `CORTEX_PLAN_SQL_CLARIFY` default off, and
   contract minor 1.12.0. The minor adds `PlanSqlReconfirm.clarify`,
   `AskRequest.plan_sql_clarify_id` and `AskRequest.plan_sql_clarify_reply`;
   "I can't tell" reuses today's Yes/No on `closest_question`.

## Repro
All of this is in the scratch harness, not committed:
- generator: `clarify.py`;
- simulated user: `user_side.py`;
- metrics: `metrics.py`;
- engine hook: `engine_patch.diff`;
- harness: `clarify_bank.py`, `runclarify.sh`, `ov_standin_cl.py`;
- raw runs: `clarify_A1` (OFF), `clarify_A3` (CLARIFY), `clarify_intents.A.jsonl`
  and `clarify_metrics.A.json`.

The orchestrating session recounted the per-arm and re-ask verdicts from the
raw per-question rows, and they match the table above.

Pending Lead adjudication: 1080 (rows vs distinct players) and 1235 (column
order). Neither changes the result.
