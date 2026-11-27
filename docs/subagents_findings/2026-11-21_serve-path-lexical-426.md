# #426: word rules decided serves in `answer()` (2026-11-21)

Serve path: `packs/dms/semantic/catalog_answer.py::is_catalog_intent`,
`packs/dms/semantic/query_skills.py::find`, `_shape_refusal`.

## Expected vs actual (main `304bf84`)

- Expected: a model chooses the governed metric or the catalog; no keyword
  rule decides what is served (CLAUDE.md s9).
- Actual: a regex ran before the metric choice, so "show me the metrics for
  on-time delivery" came back as the catalog listing. With
  `CORTEX_QUERY_SKILL=1`, a stored trigger sharing enough words served its
  metric: after "how many delayed shipments" was stored, "how many not delayed
  shipments" served 1031 under the `query_skill` badge.

## Repro

`python -m pytest tests/dms/test_serve_path_lexical_426.py -q` on main's
`catalog_answer.py`, `metric_choice.py` and `query_skills.py`.

## Root-cause class

**A lexical decider on the serve path.** Word overlap stood in for meaning
at the point that picks what is served.

## Fix

- `CORTEX_CATALOG_BY_MODEL` (default on): the model that chooses the metric
  may choose the catalog, a second call checks it, and the decision is handed
  to `decide_metric` once per ask. A certified question skips the model; a
  failing model falls back to main's path with no handoff.
- A skill replays only for the same question, and only when `answer()`'s
  replay (params minus `REPLAY_DROPS`) compiles the SQL it captured.
- Attacker round on `1a2fd87`: the replay rule called
  `answer_engine._sales_rank_slots` from the pack, which broke the #340
  boundary (pack side reaches the engine only through `ask_scope`), and it
  refused the stale-exclusion replay `test_q2_answer_engine` requires. The
  call is gone; sales-rank metrics replay as on main.

## Invariants that applied

- Keyword cascades and word rules stay out of the answer path.
- A control that blocks legitimate work is itself a failure: the first replay
  rule refused faithful replays; the second broke an import boundary.

## Open, by name

- QUERY-SKILL-REREAD-WORD-RULE: for sales ranks the frozen `answer()` reads
  limit, direction, window and exclusions again from the question with
  `_sales_rank_slots`. "the 3 least sold products" replays as limit 5. Two
  strict xfails in `test_serve_path_lexical_426.py` hold it. Needs a founder
  seam in `answer()` (#215). Reachable only with `CORTEX_QUERY_SKILL=1`
  (default off).
- SESSION-ANAPHORA word rules in `answer_engine.py` (frozen): "2024/05" read
  as divide by 5 on an ungrounded caller. P0 class, founder seam needed.
- `synthesize_answer` substring branches and the L2 plausibility regexes
  (frozen files).
