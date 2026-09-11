# C7-06: retire route_to_metric as serve chooser (gated)

- Date: 2026-09-04
- Keywords: C7-06, route_to_metric, choose_governed_metric, cascade, cutover, G-err, G-abs, DMS_C7_RETIRE_CASCADE
- Main idea: `answer()` uses `choose_governed_metric`. The 27-regex cascade still serves. Retirement requires L2-as-L1-replacement beating L1 on G-err with G-abs holding plus `DMS_C7_RETIRE_CASCADE=1`. A flag or JSON `cutover: true` cannot invent-green. 25 `_metric_plan` branches not deleted.
- Path: this file

## PREFLIGHT

PARTIAL. reuse: `docs/design/2026-09-04_C7_ROUTE_TO_METRIC_REPLACEMENT.md` C7-06, `2026-09-04_c7-design-and-shadow-mode.md`, `2026-09-04_c7-04-heldout-harness.md`, `2026-09-04_c7-05-engine-scorer.md`. spawn: skip (executor). Rebased onto origin/main after C7-05 #126/#143 landed.

## Golden rules

1. Do not delete the 25 `_metric_plan` branches without held-out numbers in the commit body.
2. `DMS_C7_RETIRE_CASCADE=1` is necessary and not sufficient.
3. L2-on-miss reports (`cascade_skipped` false) cannot retire the chooser.
4. `cutover` stays false in this module. C7-05 owns serve-on-miss cutover.
5. Keyword slot helpers stay. L0 certified still serves when the cascade is skipped.

## Verify

```
python -m pytest tests/dms/test_c7_06_retire_cascade.py tests/dms/test_q2_answer_engine.py tests/dms/test_c7_l2_shadow.py tests/dms/test_c7_full_generation.py -q
python -m bench.heldout --engine
```

Does not prove: live FreeRoute L2-as-L1-replacement, G-sh >= 500, or that the cascade can be deleted.

## Live L1 after rebase onto C7-05 main (2026-09-04)

`python -m bench.heldout --engine` (DMS_L2_ENABLED unset): 28 items, 1 correct / 27 abstained / 0 incorrect. G-abs/G-err/G-env true. G-sh 0 lines. `cutover` false. This is **L1 cascade**, not L2-as-L1-replacement (`cascade_skipped` absent). Do not undraft.

