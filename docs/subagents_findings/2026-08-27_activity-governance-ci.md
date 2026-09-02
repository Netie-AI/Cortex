---
keywords: [activity, governance, ledger, control, ontology_bench, belt, ecosystem]
main_idea: Cortex GET /api/engine/activity now emits governance (ledger tip, bound sessions, refusals, no payloads). DMS fixture ontology_bench is already collected by pytest tests/ (6 passed). Goal open.
models: [cursor-grok-4.6]
workflow: 2026-08-27_activity-governance-ci
reuse: golden_rule
status: verified
cite: distill: E:\Netie\Internal\Workflow\ECOSYSTEM_EXECUTION_PLAN.md#6
repo: Cortex
date: 2026-08-27
---

# Activity governance + CI honesty (2026-08-27)

PREFLIGHT: HIT - belt-proxy, analog-nearness, recon-rank rows. Control STATUS already claimed activity.governance; Cortex activity_routes.py did not emit it.

## Main idea

Control `:8040` reads Cortex `/api/engine/activity` and will not scrape the ledger. The engine now returns a fault-isolated `governance` section (registered/tip/recent, bound session ids, refusal events) with payloads stripped. DMS `tests/test_ontology_bench.py` (6 fixture cases, no parquet lake) is already collected by `pytest tests/`. The 896-case CLI is still not a GitHub job.

## Golden rule

> Display identifiers and verdicts. Never put ledger payloads on the operator panel.

## Verify

```
python -m pytest tests/dms/test_agent_engine_routes.py tests/dms/test_app_runner.py::test_activity_lists_running -q
python -m pytest E:\NetieControl\tests -q
python -m pytest E:\Netie-Crew\tests -q
E:\DMS\.venv\Scripts\python.exe -m pytest tests/test_ontology_bench.py -q
```
