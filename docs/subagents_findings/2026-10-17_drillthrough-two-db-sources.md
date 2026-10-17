# Drillthrough read provenance from one database and rows from another (2026-10-17)

Found in CI on #409 (head b7be3c1) and fixed in c0526c2. Merged as afa426c.

## Expected vs actual
- **Expected:** a drill of `SELECT sku, SUM(qty) AS total FROM stock GROUP BY sku`, on a lake whose `stock` table has `_src_ref_id`, returns rows carrying `stock__src_ref_id` (r1, r2, r3).
- **Actual:** the rows came back without any provenance column, and the test failed with `KeyError: 'stock__src_ref_id'`. The values themselves were still correct; only the provenance was missing.

## Repro
1. Check out b7be3c1.
2. Run `python -m pytest tests/dms/test_name_echo_scrub.py::test_drillthrough_success_is_unchanged -q`.
3. Result: 1 failed. CI lint-type-test failed the same way: 1 failed, 3892 passed.

## Root cause
- `_warehouse_provenance_columns` asked `CortexOS.execution.warehouse.DEFAULT_DB` which provenance columns each table has.
- `execute_sql` runs the drill against `CortexOS.execution.submit.DEFAULT_DB`, a separate module binding that tests and deployments can point elsewhere.
- When the two differ, the lookup checks a database that lacks the table, finds nothing, and projects no provenance.
- The fix reads `submit.DEFAULT_DB` at call time, so the metadata lookup and the executor share one source.

## Root-cause class
Two code paths each resolve "the warehouse" on their own: one reads metadata, the other executes. Any pre-check (provenance, column listing, grant probe) must use the same database handle or path as the executor it guards. Otherwise the check describes a different database than the one it guards.

## Invariant
"Silent fallback is a lie." The lookup swallowed the mismatch (`except Exception: return []`) and served rows without provenance, with nothing in the output to show it. The positive-control test asserting the artifact the customer receives caught it, not the code.

## Reach
- **Verified:** seven other modules bind `DEFAULT_DB` at import time: brain_routes, l2_generation, entry_analyser, query_service, warehouse_db, answer_engine and drillthrough. Each one copies `warehouse.DEFAULT_DB`, which comes from `DMS_WAREHOUSE_DB`.
- **Inferred:** in a deployment every binding holds the same path, so the split only shows when one binding is rebound, as tests do. That makes this a test-seam defect, not a live one. Where a test points `submit` at a lake, the engine must still check that same lake.
