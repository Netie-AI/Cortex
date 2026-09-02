---
keywords: [crew, converse, EventSource, conn, plugins, mcp, r-0011, unread, reconnecting, stolen.css]
main_idea: Converse UI names EventSource reconnecting/unread, plugins unread vs none, MCP unread vs none. Graph already graph none. One composer. stolen.css 410. Live :8020 still the fork.
models: [cursor-grok-4.6]
workflow: 2026-09-03_crew-converse-r0011-conn-plugins-mcp
reuse: golden_rule
status: verified
cite: distill: docs/subagents_findings/2026-09-03_crew-inspector-wakes-queue-r0011.md
repo: Cortex
date: 2026-09-03
---

# Crew converse remaining R-0011: conn / plugins / MCP (2026-09-03)

PREFLIGHT: HIT - inspector-wakes-queue-r0011, inspector-skills-tickets-r0011, crew-product-gaps-analog-placeholders.

## Hunt

| Item | Verdict | Path |
|---|---|---|
| EventSource error leaves conn live | ALREADY onerror -> setConn(false) = reconnecting. Chip starts unread. Added close-while-live -> reconnecting so space switch is not a live lie | `ui/index.html` listen/setConn |
| plugins list empty on failed GET | Fixed. Catch `{ unread: true }`. Failed/not-yet = plugins unread. Idle = plugins none | `ui/index.html` renderPlugins |
| MCP pane empty / master Off on failed GET | Fixed. Catch `{ unread: true }`. Failed/not-yet = mcp unread. Idle = mcp none. Do not claim master Off when unread | `ui/index.html` renderMcp |
| empty graph | ALREADY. `graph none` | `ui/index.html` |
| second composer | ALREADY. One `<div class="composer">` | `ui/index.html` |
| stolen.css | ALREADY. GET `/stolen.css` 410 | `server.py` (untouched) |

## Still true

Live `:8020` is the Cortex-crew fork. Do not kill it (R-0015). This UI is `E:\Cortex\CortexOS\crew\ui`. Control does not POST `/v1/run`. Do not grow `E:\Cortex-crew`.

## Verify

```
cd E:\Cortex
PYTHONPATH=E:\Cortex python -m pytest tests/test_crew/test_server.py -q
```

12 passed.
