---
keywords: [crew, control, ui, roster, belt, grok-bot, port-8020, inspector]
main_idea: Crew chat UI was losing :8020 to the Netie-Crew table host. Cortex Crew now serves /v1/belt, seeds idle specialists, and chips are clickable. Control is a one-panel app with filter and Crew launch. Still zero grok-bot bytes.
models: [cursor-grok-4.6]
workflow: 2026-08-28_crew-control-app-ui
reuse: golden_rule
status: verified
cite: distill: docs/subagents_findings/2026-08-23_crew-stolen-ui.md
repo: Cortex
date: 2026-08-28
---

# Crew + Control feel like apps (2026-08-28)

PREFLIGHT: HIT - stolen-ui, control-crew-belt, crew-harness-patterns.

## Main idea

Grok Bot reconstruction is still not vendored. The missing UI was port theft
(:8020 conveyor vs chat) plus `pointer-events: none` on specialist chips.
Cortex Crew seeds the idle roster, owns `/v1/belt`, and Control switches one
panel at a time with filter + Crew launch iframe. No converse in Control.

## Golden rule

> One process on :8020. Chat is Cortex Crew. Control displays and launches.

## Verify

```bash
python -m pytest tests/test_crew/test_harness.py tests/test_crew/test_config_policy.py -q
python -m pytest E:\NetieControl\tests -q
python -m pytest E:\Netie-Crew\tests -q
```
