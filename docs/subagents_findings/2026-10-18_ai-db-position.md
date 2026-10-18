# AI database analyst position, 2026-10-18

Shas read: Cortex main 9936595c, dms main 8102f15a (after #449).

## Finding: the stub 52 cannot see the AI lane, so ai=0 there is by construction

- Expected: the 52-question grade measures what the product serves, model SQL included.
- Actual: the stub insights call always returns `insights_unarmed`
  (dms scripts/verify/capture_layout.py:117), the grade52 stub replays
  `{"ontology":{"metrics":[]}}`, and tests/fixtures/ask_guide/cortex_responses_52.json
  holds 52 placeholder replies (`SELECT 1 AS n`). "correct=16 wrong=0 abstain=27,
  ai=0 rule=16" says nothing about the AI lane.
- Repro: `git grep -n insights_unarmed origin/main -- scripts/verify/capture_layout.py`
  in dms; read the replay fixture.
- Root-cause class: a gate that cannot fail. The harness cannot observe the property
  it is quoted for.
- Invariants: "Prove a gate can fail before trusting it green" and "Assert the
  artifact the user actually receives".

## Facts (read in code or findings files)

- Model SQL is not the serve path. Cortex plan+SQL needs `CORTEX_PLAN_SQL=1` and a
  `dms_payload` (CortexOS/dms/plan_sql_ask.py:3,72); dms main pins contract 1.5.0
  and its generated models carry `dms_payload`, but the executor never sends it
  (dms #452 would). DMS answers rules first: verified query, PACK_METRICS, bronze,
  then generative; Cortex L1 is the route_to_metric keyword cascade (#313 open).
- Only live numbers: pure model-SQL scratch harness 20/26 held-out on the 52 pack,
  BIRD Mini-Dev 50.4% (n=498, 222 wrong), 2026-10-09
  (2026-10-09_bird-pure-l2.md). No live score of the product exists.
- Connectors: CSV, XLSX, and SQL Server/MySQL/Postgres snapshot extracts into DuckDB
  (500k-row cap, closed SourceKind list, ADR 0005 extract-only). No Snowflake,
  BigQuery, Sheets or API sources. Cortex /api/connectors is agent tooling, not data.
- Charts: hand-written SVG, bar/hbar/line/bignum, chosen by rules in three places.
  BI export is a Power Query snippet or Superset dataset file; no live BI route.

## External reference points (web, cited in session report)

BIRD test top 82.95% (2026-09-07); LiveSQLBench best agent 48.00%. Leaders ground in
semantic layers plus verified queries and have the model emit Vega-Lite. None of the
leader docs read publish a wrong-answer rate; Cortex Analyst alone documents an
explicit abstain.
