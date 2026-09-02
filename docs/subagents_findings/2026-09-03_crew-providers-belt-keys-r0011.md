---
keywords: [crew, providers, belt, keys, computer, r-0011, vault_listing, probe_vault, not probed]
main_idea: GET /crew/providers skips live vault listing. GET belt skips engine ping. Keys empty fields and host/search name unread vs none. Live :8020 still fork.
models: [cursor-grok-4.6]
workflow: 2026-09-03_crew-providers-belt-keys-r0011
reuse: golden_rule
status: verified
cite: distill: docs/subagents_findings/2026-09-03_crew-health-ov-once.md
repo: Cortex
date: 2026-09-03
---

# Crew providers/belt/keys first-paint fail-close (2026-09-03)

PREFLIGHT: HIT - INDEX already covers resolve_providers no healthz (F-0040), engine/voice unread, desk GH 1.5s catalog estate, roles/plugins/mcp/wakes/queue unread, stolen.css 410, analog CGNAT/.local, PEER_HEALTH 1s.

Seated write-target `E:\Cortex\CortexOS\crew`. Did not grow `E:\Cortex-crew`. Did not restart `:8020` or `:8023`. Did not rebuild queue.py / wakes.py / stall.py.

## COPY / DISTILL / SKIP / ALREADY

| Item | Verdict | Path |
|---|---|---|
| GET /crew/providers calls vaulted_provider_ids via prefer+wizard | DISTILL. First paint skips live vault (`probe_vault=False`, `vault_listing=skipped`). Env flags still paint. Chat `resolve_ov_model` still probes. | `openvault.py` `server.py` `ui/index.html` |
| GET /crew/belt and /v1/belt await _engine_health | DISTILL. Belt is local conveyor. `cortex.detail=not probed`. Control already GETs /crew/health for engine_ok. | `server.py` `_belt_now` |
| Keys empty fields look like no form | DISTILL. Empty `fields` paints keys unread, not a blank form. | `ui/index.html` renderKeyForm |
| Host panel no unread vs disarmed | DISTILL. `#hostStatus` starts unread, then failed/none/disarmed/armed from GET /crew/mcp. | `ui/index.html` paintHost |
| Search failed GET paints No results | DISTILL. Failed search is unread. Idle is Type two characters / search none. | `ui/index.html` runSearch |
| GET /crew/health PEER_HEALTH 1s | ALREADY | `server.py` |
| resolve_providers no healthz | ALREADY | `config.py` |
| Inspector skills/tickets/desk/roles/wakes/queue unread | ALREADY | `ui/index.html` |
| queue.py wakes.py stall.py | ALREADY. Do not rebuild. | -- |
| Guaca / Beads / LangGraph | SKIP | -- |
| COPY gastown/openworker bytes | SKIP. LICENSE missing on those clones. | -- |

## Files

- `E:\Cortex\CortexOS\crew\openvault.py`
- `E:\Cortex\CortexOS\crew\server.py`
- `E:\Cortex\CortexOS\crew\ui\index.html`
- `E:\Cortex\tests\test_crew\test_server.py`
- `E:\Cortex\tests\test_crew\test_openvault.py`

## Verify

26 passed: `cd E:\Cortex; $env:PYTHONPATH=E:\Cortex; python -m pytest tests/test_crew/test_server.py tests/test_crew/test_openvault.py -q`

## Leftover founder bind

Live `:8020` is still the Cortex-crew fork. YOU step 8: founder restarts `python -m CortexOS.crew` from `E:\Cortex`. Agents do not kill it (R-0015). Engine tree is this seating. Control still probes `:8020`.
