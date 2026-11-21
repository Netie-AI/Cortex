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
Fourteen variants, deterministic by seed and named by id: row samples at 5%,
20%, 50% (two seeds each), numeric jitter of plus or minus 50% (two seeds),
and six bitmask skews that thin some tables and keep others whole. The skew
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

## Open, by name

- Pending: the three-attacker round on `1a2fd87` was running when this was written; its results go in PR #481 and in a follow-up to this file.
