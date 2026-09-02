---
keywords: [crew, inspector, engine, voice, r-0011, unread, apiGet, stolen.css, composer]
main_idea: Failed GET /crew/health no longer paints engine offline / vault down / computer-control master off. Voice failed GET names unread, uses apiGet 2.5s. One composer. stolen.css 410. Live :8020 still the fork.
models: [cursor-grok-4.6]
workflow: 2026-09-03_crew-engine-voice-unread
reuse: golden_rule
status: verified
cite: distill: docs/subagents_findings/2026-09-03_crew-desk-gh-2s-parallel.md
repo: Cortex
date: 2026-09-03
---

# Crew engine/voice remaining R-0011 unread vs offline (2026-09-03)

PREFLIGHT: HIT - INDEX already covers desk GH_WAIT_S 2s, roles/agents/todos/confirms unread, plugins/MCP unread, wakes/queue unread, stolen.css 410, one composer, analog CGNAT/.local/instance-data, health PEER_HEALTH 1s, desk catalog estate live=False.

Seated write-target `E:\Cortex\CortexOS\crew`. Did not grow `E:\Cortex-crew`. Did not restart `:8020` or `:8023`. Did not rebuild queue.py / wakes.py / stall.py.

## COPY / DISTILL / SKIP / ALREADY

| Item | Verdict | Path |
|---|---|---|
| Failed GET /crew/health paints engine offline + master off | DISTILL. R-0011. Now unread until health.ok is true. Probed-down still says engine offline. | `ui/index.html` renderEngine |
| Voice catch uses err.message; api not apiGet | DISTILL. Failed GET = voice unread. Idle success = server reason or voice none. apiGet 2.5s. | `ui/index.html` openVoice |
| Inspector first-paint skills/tickets/desk/routines/roles/agents/todos/confirms/wakes/queue | ALREADY unread vs none | `ui/index.html` |
| plugins/MCP unread, conn reconnecting, graph none | ALREADY | `ui/index.html` |
| apiGet 2.5s vs server probes | ALREADY. GH_WAIT_S 2s, PEER_HEALTH 1s. 8s OpenVault timeouts are POST key upsert, not GET boot. estate_status stays chat-driven. | `github.py` `server.py` `openvault.py` |
| composer count 1 | ALREADY | `ui/index.html` |
| GET /stolen.css 410 | ALREADY | `server.py` |
| SSE setConn live/reconnecting; chip starts unread | ALREADY | `ui/index.html` listen |
| queue.py wakes.py stall.py | ALREADY. Do not rebuild. | -- |
| Guaca / Beads / LangGraph | SKIP | -- |
| COPY | none | Gas Town LICENSE missing |

## Still true

Live `:8020` is the Cortex-crew fork. Do not kill it (R-0015). This UI is `E:\Cortex\CortexOS\crew\ui`. Control does not POST `/v1/run`. Founder bind `:8020` is leftover, not this seating.

## Verify

```
cd E:\Cortex
$env:PYTHONPATH="E:\Cortex"
python -m pytest tests/test_crew/test_server.py -q
```

12 passed.
