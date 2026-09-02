---
keywords: [ontology, plan-header, r-0011, o1, o4, parking-lot, honesty]
main_idea: CORTEX_ONTOLOGY_PLAN.md said PLAN ONLY while O1-O5/O7 exist. Header fixed as honesty, not a P1 unpark. Goal open.
models: [cursor-grok-4.6]
workflow: 2026-08-28_ontology-plan-header
reuse: golden_rule
status: verified
cite: distill: E:\Netie\Internal\Workflow\ECOSYSTEM_EXECUTION_PLAN.md#2
repo: Cortex
date: 2026-08-28
---

# Ontology plan header honesty (2026-08-28)

PREFLIGHT: HIT - founder card named the stale PLAN ONLY header. Pointer/Space/Control belt/Crew host/constructor ghost/TAS-CONTROL ancestry not redone.

## Main idea

O1 YAML+registry, O2 builder+CLI (compiled db gitignored), O3 F8 allowlist, O4 CortexOS/agent_sdk, O5 sidecar, O7 new_pack.py are on disk and PASS in STATUS.md. The plan header still said nothing is built. Header now names shipped vs O6/O8/P1. Regression test locks it.

## Golden rule

> A plan header that denies shipped artifacts is a lie. Fixing it is not an unpark.

## Verify

```
python -m pytest tests/dms/test_ontology_registry.py -q
```

## Do not

- dms#61, HT1-HT5, work.netie.ai, grok-bot copy, P1/P17/P18 unpark, second dag_runner
