# C7 route_to_metric replacement design + L2 shadow

- Date: 2026-09-04
- Keywords: C7, EPIC-006, route_to_metric, DMS_L2_SHADOW, L2, manifest, EXPLAIN, abstain-confidence, held-out, Spider, BIRD
- Main idea: L1 is a 27-`re.search` / 25-`MetricPlan` cascade. L2 pipeline already exists behind `DMS_L2_ENABLED` (default OFF) on an engine port. Do not swap serve yet. C7-01 shadows L2 after the served envelope (`DMS_L2_SHADOW=1`); never mutates the answer. Manifest must run on every generated SQL before EXPLAIN (C7-02). Cutover needs a non-team held-out corpus plus numeric gates.
- Path: this file

## PREFLIGHT

PARTIAL. reuse: `docs/dms/ROUTER_STATES.md`, `docs/dms/packets/CORTEX_TO_DMS_C7_KICKOFF.md`, `docs/dms/DMS_EVAL_AND_STRESS_PLAN.md` §3, `docs/subagents_findings/2026-08-25_dms-handoff-f40-ff03.md`. spawn: skip (executor).

## Golden rules

1. CortexOS must not statically import `packs.*`. Call L2 only via `CortexOS.dms.l2_generation`.
2. Never weaken `CortexOS/execution/manifest.py` refusals. Enforcer before EXPLAIN.
3. Shadow (`DMS_L2_SHADOW`) must not set `DMS_L2_ENABLED` and must not promote to L0.
4. Held-out items are not team paraphrases of `metrics.yaml`. Must-abstain comes from a different model.
5. Customer-envelope assertions (answer text + rows + badge), not SQL-only gates.

## Verify

```
D:\Cortex\.venv\Scripts\python.exe -m pytest tests/dms/test_c7_l2_shadow.py tests/dms/test_c7_full_generation.py tests/dms/test_sql_validate_gate.py -q
```

Does not prove: live FreeRoute quality, held-out accuracy, or that L1 can be deleted.
