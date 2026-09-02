---
keywords: [control, crew, belt, conveyor, display-only, converse, 405, ecosystem]
main_idea: Control proxies Crew GET /v1/belt as display-only JSON. Converse stays on Crew :8020 until NETIE.md amends. 405s unchanged. Goal open.
models: [cursor-grok-4.6]
workflow: 2026-08-27_control-crew-belt-proxy
reuse: golden_rule
status: verified
cite: distill: E:\Netie\Internal\Workflow\ECOSYSTEM_EXECUTION_PLAN.md#1
repo: NetieControl
date: 2026-08-27
---

# Control Crew belt proxy (2026-08-27)

PREFLIGHT: PARTIAL - INDEX had analog-nearness + crew/control rows; analog table still said Cortex internals / Crew unbuilt.

## Main idea

One operator page without a second engine. Control `:8040` GETs Crew `:8020` `/v1/belt` (loopback only), renders tickets/handoffs/plan as display-only, and exposes the same JSON at Control `GET /v1/belt`. Converse/handoff stay labeled on Crew. NETIE.md still display-and-launch. `DR-PROPOSED-control-converse.md` is not law.

## Golden rule

> Display Crew JSON. Do not converse in Control until the charter PR merges.

## Verify

```
python -m pytest E:\Netie-Crew\tests -q
python -m pytest E:\NetieControl\tests -q
python -m pytest e:\Cortex\tests\dms\test_agent_engine_routes.py::test_activity_control_panel -q
```
