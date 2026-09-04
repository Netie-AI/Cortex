# C7-05: live held-out engine scorer (no serve cutover)

- Date: 2026-09-04
- Keywords: C7-05, held-out, score_engine, G-abs, G-err, G-env, G-sh, DMS_L2_ENABLED
- Main idea: `bench/heldout.py --engine` scores frozen items via `answer()` envelopes and reports G-abs/G-err/G-env. `--enable-l2` is process-local and restored. `cutover` stays false. Do not set `DMS_L2_ENABLED` as the serve default until G-man + G-sh also pass on a real report.
- Path: this file

## PREFLIGHT

PARTIAL. reuse: `2026-09-04_c7-04-heldout-harness.md`, `2026-09-04_c7-03-plausibility.md`. spawn: skip (executor).

## Verify

```
D:\Cortex\.venv\Scripts\python.exe -m pytest tests/dms/test_c7_heldout.py -q --tb=short
```

Does not prove: live FreeRoute quality, G-sh >= 500 shadow lines, or L2-on-miss serve.

## Live L1 baseline (2026-09-04, DMS_L2_ENABLED=0)

Live L1 after compositional L1 abstain (2026-09-04): 28 items, 1 correct / 27 abstained / 0 incorrect. G-abs, G-err, G-env true. G-sh 0 lines. G-man: `pytest tests/test_execution` 228 passed. `cutover` stays false.
