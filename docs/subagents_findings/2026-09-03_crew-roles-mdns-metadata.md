---
keywords: [crew, r-0011, roles, agents, todos, confirms, unread, analog, ssrf, instance-data, mdns, local]
main_idea: Roles/agents/todos/confirms now name unread vs none. analog/research refuse instance-data and .local. IPv6/RFC1918/metadata.google.internal already at-bar via is_global. Live :8020 still the fork.
models: [cursor-grok-4.6]
workflow: 2026-09-03_crew-roles-mdns-metadata
reuse: golden_rule
status: verified
cite: distill: docs/subagents_findings/2026-09-03_crew-product-gaps-ingest-cgnat.md
repo: Cortex
date: 2026-09-03
---

# Crew remaining gaps: roles unread + metadata hostname (2026-09-03)

PREFLIGHT: HIT - INDEX already covers ingest-cgnat, inspector R-0011, analog loopback, plugins/MCP, TAS gaps.

Seated Control GET /v1/contract then pickup/fleet/you. Did not POST /v1/run. Did not grow E:\Cortex-crew. Did not restart :8020 or :8040.

## COPY / DISTILL / SKIP / ALREADY

| Item | Verdict | Path |
|---|---|---|
| roles empty chip bar | DISTILL. Failed GET was `[]` then renderRoles returned. Now unread vs none. | `CortexOS/crew/ui/index.html` |
| agents/todos/confirms idle lie | DISTILL. First paint and failed GET said none/pending. Now unread vs none. | `ui/index.html` `crew.css` |
| instance-data / .local analog | DISTILL. Hostname not an IP so is_global never ran. | `research.py` `analog.py` |
| IPv6 ::1, mapped loopback, RFC1918, metadata.google.internal, 192.0.0.192 | ALREADY. `not ip.is_global` / `.internal`. Tests added. | `research.py` |
| belt/health PEER_HEALTH_WAIT_S=1.0 | ALREADY. Hang test < 2.5s. Did not restore 5s sequential. | `server.py` |
| stolen.css 410, one composer, /crew.css | ALREADY | `server.py` `ui/` |
| queue.py wakes.py stall.py a2a.py | ALREADY. Do not rebuild. | -- |
| Guaca / Beads / LangGraph | SKIP | -- |
| COPY | none | Gas Town LICENSE missing |

## Still true

Live `:8020` health times out, `/crew/wakes` 404, `/crew.css` 404 -- Cortex-crew fork. Spare `:8023` serves engine Crew JSON. Do not kill either (R-0011 / R-0015). Control does not POST `/v1/run`.

## Verify

```
cd E:\Cortex
PYTHONPATH=E:\Cortex python -m pytest tests/test_crew/ -q
# 407 passed
```
