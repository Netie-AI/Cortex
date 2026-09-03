---
keywords: [crew, wakes, queue, belt, 8020, control, 405, mailbox]
main_idea: Origin/main CortexOS/crew had store and A2A mailbox but no GET /v1/belt or /crew/wakes. Control already probes those. Wire thin wakes.py and queue.py onto the existing store. Not a second mailbox. Not LangGraph. Control never POSTs wakes.
models: [cursor-grok-4.6]
workflow: 2026-09-03_crew-wakes-queue-wired
reuse: golden_rule
status: verified
cite: Cortex#97
repo: Cortex
date: 2026-09-03
---

# Crew belt/wakes stay on CortexOS/crew

PREFLIGHT: PARTIAL. INDEX had control-crew-belt-proxy (file missing). Control sources.py is the consumer contract.

## Main idea

- Write-target: `CortexOS/crew`. Not Cortex-crew. Not a second engine.
- `GET /v1/belt` preferred, `GET /crew/belt` fallback, `GET /crew/wakes` talk probe.
- Belt JSON: bus=github-issues, wakes, queue counts, confirms, spaces, agents, cortex.detail=not probed, plan_for_next.decides_work_shape=false.
- POST on those paths is 405. Control never POSTs wakes.
- Mailbox cursors stay on `a2a.Mailbox.drain(after_seq)`. No stall.py.
- Hung converse :8020 is not started or killed (R-0015).

## Golden rule

> Crew owns the tick and the lease. Control GETs the conveyor. Do not invent a second mailbox or LangGraph rewrite.

## Verify

```bash
python -m pytest tests/test_crew/test_wakes.py tests/test_crew/test_queue.py tests/test_crew/test_store.py tests/test_crew/test_server.py -q
```
