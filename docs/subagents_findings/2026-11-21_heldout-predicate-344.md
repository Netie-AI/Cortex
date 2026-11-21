# #344: a dropped filter scored correct on the held-out suite (2026-11-21)

Scorer: `bench/heldout.py::score_envelope`.

## Expected vs actual (main `304bf84`)

- Expected: served SQL that drops a predicate scores incorrect.
- Actual: `SELECT country FROM suppliers GROUP BY country` (no
  `HAVING AVG(lead_time_days) > 20 AND COUNT(*) >= 3`) scored correct on
  `spider_country_mean_lead_having`: every country in the demo database passes
  the HAVING, so the result sets match.

## Repro

`tests/dms/test_heldout_predicate_344.py::test_the_issue_case_matches_on_the_base_database`
shows the base match; with main's `bench/heldout.py` 21 of 23 tests fail.

## Root-cause class

**One database as the oracle.** A result-set match on a single database
cannot tell a missing predicate from a correct one when the data happens to
satisfy it.

## Fix

Test-suite accuracy: an item with gold SQL is correct only if the served SQL
returns the gold rows on every seeded variant of the tables it reads.
Eighteen variants, deterministic by seed and named by id: row samples at 5%,
20%, 50% (two seeds each), numeric jitter of plus or minus 50% (two seeds),
`novel` values (two seeds), `edge` values (two seeds), and six bitmask skews
that thin some tables and keep others whole. The skew
family was added after the first cut missed
`bird_inbound_shipments_gt_sku_distinct`, a predicate comparing two tables'
counts that uniform sampling cannot move. A served answer with no `sql_used`
is `variant_unverifiable:no_sql_used`; abstain scoring and `expected_rows`
items are unchanged.

## Invariants that applied

- Prove a gate can fail: the variant check off in `score_envelope` leaves the
  issue's case green; the skew variants off leave the cross-table case green.
- No false incorrect: every held-out gold SQL scores correct against itself
  on all 14 variants.
- Not run: held-out totals on a live engine (needs OpenVault).

## Attacker round on `1a2fd87` (three attackers)

- BYPASS: five wrong SQLs scored correct on all 14 variants. Sampling and
  scaling never produce a value the base lacks, so `status NOT IN (<the other
  four>)` for `= 'DELAYED'`, a date cutoff moved two days or into an empty gap,
  and a dropped `reorder_level_kg > 0` all matched. Fixed with two families:
  `novel` (an enum-like string becomes an unseen token, dates shift up to 60
  days, signed numbers flip sign) and `edge` (dates and numbers set to the gold
  SQL's own literals, plus or minus one). All five now score
  `variant_mismatch:novel0.3-s1` or `edge0.3-s1`.
- BYPASS: the skew bit index ran over the sorted union of gold and served
  tables, so an extra table, or `SHIPMENTS` in capitals, moved the thinned
  table and the cross-table mutant scored correct. Fixed: only the gold's
  tables vary, names are lowercased, the index counts only tables that exist.
- False incorrect: a correct inner join to a dimension the gold does not read
  scored `variant_mismatch:keep0.05-s1` (the sample emptied `locations`).
  Fixed: tables only the served SQL reads are copied whole.
- GAP: `bench/lanes.py::_grade_data` dropped `sql_used`, so every lanes data
  item was `variant_unverifiable` and graded WRONG. Fixed.
- GAP: the shadow L2 envelope dropped `l2_sql`. Fixed. A shadow line has no
  served SQL (the producer, `CortexOS/dms/l2_generation.py`, is #215-frozen), so
  its lines are counted `unscored`, not as a loss.
- Minor: a scoring exception aborted the whole report. Each item is now
  scored under `_score_or_error` and named.

Root-cause class of the round: **sampling only re-weights values the oracle
already has.** A variant family that only drops rows or scales numbers cannot
separate predicates that agree on every subset of the existing values.

## Open, by name

- HELDOUT-FK-SAMPLE: when the gold joins a parent table and a served SQL
  relies on foreign-key integrity instead, a sample of the parent can score a
  correct answer incorrect. Not seen on the held-out set; low confidence.
- HELDOUT-SHADOW-NO-SERVED-SQL: shadow lines cannot be variant-checked until
  the frozen producer logs the served SQL.
- Not run: held-out totals on a live engine (needs OpenVault).
