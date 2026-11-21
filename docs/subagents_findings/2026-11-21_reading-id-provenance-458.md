# #458: a substring check over a random reading id flaked (2026-11-21)

Test: `tests/test_plan_sql/test_plan_sql_pick_reading.py::_assert_readings_offered`.

## Expected vs actual (main `304bf84`)

- Expected: the check proves a served `reading_id` carries nothing from the
  SQL (no table or column name).
- Actual: it searched the id for table words. The id is
  `secrets.token_urlsafe(16)`, which held "sku" by chance
  (`-kp7ssku-cdskxa0gdslzw`, #447) and failed a correct run.

## Repro

`test_must_pass_a_minted_id_that_happens_to_hold_a_table_word` with main's
substring check: red on an opaque id.

## Root-cause class

**A lexical assertion over random input.** The property was "the id comes
from the minter", and the test checked "the id does not spell a word".

## Fix

Tests mint ids with a counter (`opaque-NNNNNN`) and the check asserts each
served id is one the store minted. One test runs the real minter and asserts
every served id is exactly a `secrets.token_urlsafe(16)` output, one call per
reading, all distinct.

## Invariants that applied

- Prove a gate can fail: an id built from the SQL (`transactions-sku-1`) is
  caught; a minted id holding "sku" passes.
- Attacker round: one HOLDS, one GAP (the real minter never ran), fixed.
  A sweep of about 20 whole-body substring checks in `tests/test_plan_sql/`
  found none that random text can trip.

## Open, by name

- None.
