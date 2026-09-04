# C7-03: Plausibility after L2 execute

- Date: 2026-09-04
- Keywords: C7-03, plausibility, empty-success, implausible_shape, retrieval miss, leftover literals, low_confidence
- Main idea: After L2 execute and before synthesize, `CortexOS/dms/l2_plausibility.py` can only pass or abstain. Empty-success, scalar-vs-listing, SQL tables disjoint from retrieval, leftover value-dict literals, and score below 0.55 become badge=abstain with empty rows. SQL is not rewritten. Enforcer is not skipped.
- Path: this file

## PREFLIGHT

HIT. reuse: `docs/design/2026-09-04_C7_ROUTE_TO_METRIC_REPLACEMENT.md` section (c), C7-02 finding. spawn: skip (executor).

## Golden rules

1. Plausibility does not generate SQL and does not call `enforce_manifest`.
2. Customer envelope: badge=abstain, rows=[], reason in assumptions.
3. Serve pre-enforce SQL from C7-02 so `execute_sql` does not double-wrap (shadowing PathNotAllowed).
4. Do not enable `DMS_L2_ENABLED` as the serve default (C7-05).

## Verify

```
D:\Cortex\.venv\Scripts\python.exe -m pytest tests/dms/test_l2_plausibility.py tests/dms/test_c7_full_generation.py tests/dms/test_c7_l2_shadow.py tests/dms/test_c7_02_manifest_before_explain.py -q
```

Does not prove C7-05 serve, live FreeRoute quality, or numeric G-abs/G-err gates.
