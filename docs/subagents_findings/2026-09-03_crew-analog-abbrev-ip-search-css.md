---
keywords: [crew, analog_clone, ssrf, inet_aton, 127.1, search, r-0011, css, first-paint, apiGet]
main_idea: analog/research refuse inet_aton loopback shortcuts (127.1, 0x7f000001) without DNS. analog_clone does not write on failed fetch. Search first-paint idle + apiGet. CSS unread wells for spaces/providerChip/searchHits. Live :8020 still fork.
models: [cursor-grok-4.6]
workflow: 2026-09-03_crew-analog-abbrev-ip-search-css
reuse: golden_rule
status: verified
cite: distill: docs/subagents_findings/2026-09-03_crew-providers-belt-keys-r0011.md
repo: Cortex
date: 2026-09-03
---

# Crew analog abbrev-IP + search first-paint (2026-09-03)

PREFLIGHT: HIT - INDEX already covers providers skip vault, belt skip engine ping, keys/host/search unread, engine/voice unread, desk GH 1.5s, roles/plugins/mcp/wakes/queue unread, stolen.css 410, analog CGNAT/.local/instance-data/loopback, resolve_providers no healthz, PEER_HEALTH 1s.

Seated write-target `E:\Cortex\CortexOS\crew`. Did not grow `E:\Cortex-crew`. Did not restart `:8020` or `:8023`. Did not rebuild queue.py / wakes.py / stall.py. Did not add DNS.

## COPY / DISTILL / SKIP / ALREADY

| Item | Verdict | Path |
|---|---|---|
| analog_clone `http://127.1/` / `0x7f000001` / decimal IP | DISTILL. ipaddress missed inet_aton shortcuts. `_literal_ip` uses socket.inet_aton, no DNS. | `research.py` `analog.py` |
| localtest.me / nip.io DNS rebinding | ALREADY parked. refuse_nonpublic_url still does no DNS. Tests pin None. | `research.py` |
| analog_clone writes on failed public fetch | DISTILL. Failed fetch looked like a successful analog. Now ok False, no write. | `analog.py` |
| GET /crew/search idle-empty first-paint | DISTILL. Palette hits started blank. First-paint Type two characters. Failed GET unread. apiGet 2.5s. | `ui/index.html` |
| analog-surface / bakeoff unread pane | ALREADY. analog-surface is a skill pack. Bakeoff is `demo/crew-landing-bakeoff` vote HTML, not a Crew GET. GET /crew/skills already unread vs none. | `skill_packs/analog-surface.md` |
| CSS first-paint unread as healthy empty | DISTILL. `#spaces.empty` `#providerChip.empty` `#hostStatus.empty` `#searchHits.empty` join the unread well. Chip starts `empty` so unread is not --mark. | `ui/crew.css` `ui/index.html` |
| apiGet 2.5s vs server probe >2.5s besides estate_status | ALREADY. health/desk/connectors/providers/GH/IMAP/PEER_HEALTH already under 2.5s. Search is local SQLite; now uses apiGet anyway. | `server.py` `desk.py` |
| queue.py wakes.py stall.py | ALREADY. Do not rebuild. | -- |
| Guaca / Beads / LangGraph | SKIP | -- |
| COPY gastown/openworker bytes | SKIP. LICENSE missing. | -- |

## Files

- `E:\Cortex\CortexOS\crew\research.py`
- `E:\Cortex\CortexOS\crew\analog.py`
- `E:\Cortex\CortexOS\crew\ui\index.html`
- `E:\Cortex\CortexOS\crew\ui\crew.css`
- `E:\Cortex\tests\test_crew\test_research.py`
- `E:\Cortex\tests\test_crew\test_analog.py`
- `E:\Cortex\tests\test_crew\test_server.py`

## Verify

14 passed: `cd E:\Cortex; $env:PYTHONPATH=E:\Cortex; python -m pytest tests/test_crew/test_server.py -q`

Also 26 passed: `tests/test_crew/test_server.py tests/test_crew/test_research.py tests/test_crew/test_analog.py`

## Leftover founder bind

Live `:8020` is still the Cortex-crew fork. YOU step 8: founder restarts `python -m CortexOS.crew` from `E:\Cortex`. Agents do not kill it (R-0015). Control still probes `:8020`.
