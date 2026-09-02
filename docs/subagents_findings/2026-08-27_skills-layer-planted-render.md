---
keywords: [skills, kb, render, planted, finding, ontology_bench, r-0011]
main_idea: Planted active rule reaches a fresh process via kb.py render. Unverified findings never render. DMS fixture ontology_bench (6) is already in pytest tests/; lake CLI is not. Goal open.
models: [cursor-grok-4.6]
workflow: 2026-08-27_skills-layer-planted-render
reuse: golden_rule
status: verified
cite: distill: E:\Netie\Internal\Workflow\ECOSYSTEM_EXECUTION_PLAN.md#3
repo: Netie-KB
date: 2026-08-27
---

# Skills render hop + DMS bench honesty (2026-08-27)

PREFLIGHT: HIT - activity-governance-ci already confirmed fixture ontology_bench in pytest tests/.

## Main idea

`kb.py render` existed; no test proved a planted lesson reaches a fresh agent. A two-test unittest now plants an isolated corpus (`NETIE_KB_ROOT`) and a second Python process reads `generated/CLAUDE.md`. Unverified findings stay out of default search and out of render. The rest of Phase 3 (feedback -> PRD table -> verified -> registry) is not this hop.

DMS STATUS no longer says "bench not in CI" as a single No. Fixture `tests/test_ontology_bench.py` (6, no lake) is collected by `pytest tests/` which GitHub ci.yml already runs. Lake CLI `scripts/ontology_bench.py` (896) is still not a GitHub job (R-0011).

## Golden rule

> Display the hop that exists. Do not stamp the full learning loop green.

## Verify

```
python -m unittest discover -s tests -q
E:\DMS\.venv\Scripts\python.exe -m pytest tests/test_ontology_bench.py -q
```
