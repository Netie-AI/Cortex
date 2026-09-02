---
keywords: [crew, import, detect, computer, r-0011, unread, idle, drop-zone, analog]
main_idea: GET /crew/import* paints drop-zone unread vs ready. Empty GET /crew/detect is idle not single_agent. GET /crew/computer names disarmed vs unread. Analog leftover parked. No loading... . Live :8020 still fork.
models: [cursor-grok-4.6]
workflow: 2026-09-03_crew-import-detect-computer-r0011
reuse: golden_rule
status: verified
cite: distill: docs/subagents_findings/2026-09-03_crew-analog-abbrev-ip-search-css.md
repo: Cortex
date: 2026-09-03
---

# Crew import/detect/computer remaining R-0011 (2026-09-03)

PREFLIGHT: HIT - INDEX already covers analog 127.1 inet_aton, analog_clone no write on failed fetch, providers skip vault, belt skip engine, keys/host/search unread, engine/voice unread, desk GH 1.5s, roles/plugins/mcp/wakes/queue unread, stolen.css 410, CGNAT/.local/instance-data, resolve_providers no healthz, PEER_HEALTH 1s, analog loopback redirect.

Seated write-target `E:\Cortex\CortexOS\crew`. Did not grow `E:\Cortex-crew`. Did not restart `:8020` or `:8023`. Did not rebuild queue.py / wakes.py / stall.py. Did not add DNS.

## COPY / DISTILL / SKIP / ALREADY

| Item | Verdict | Path |
|---|---|---|
| GET /crew/import* 405; drop-zone first-paint looks live | DISTILL. GET /import + /import/mail + /imports. First-paint import unread. Failed GET unread. Idle files_waiting 0 is import none. Drop copy after ok. | `server.py` `ui/index.html` |
| GET /crew/detect empty q is single_agent | DISTILL. Idle empty looked like a detected job. plan("") is pattern idle. Boot GET /crew/detect. Failed GET is detect unread, not idle. | `detect.py` `ui/index.html` |
| GET /crew/computer 405; host inferred from mcp | DISTILL. GET /computer returns master/armed/detail. Failed GET unread. Live master-off is disarmed. | `server.py` `ui/index.html` |
| analog besides 127.1 / CGNAT / .local / instance-data / loopback redirect / no-write-on-fail | ALREADY. IPv4-mapped, inet_aton 0 / decimal / hex pinned. DNS-rebinding names parked (localtest.me / nip.io return None). Do not add DNS. | `research.py` `tests/test_crew/test_research.py` `tests/test_crew/test_analog.py` |
| remaining loading... in ui/ | ALREADY. No loading... or checking... in `ui/`. `test_ui_index_is_served` pins both. | `ui/index.html` `tests/test_crew/test_server.py` |
| queue.py wakes.py stall.py | ALREADY. Do not rebuild. | -- |
| Guaca / Beads / LangGraph | SKIP | -- |
| COPY gastown/openworker bytes | SKIP. LICENSE missing. | -- |

## Files

- `E:\Cortex\CortexOS\crew\detect.py`
- `E:\Cortex\CortexOS\crew\server.py`
- `E:\Cortex\CortexOS\crew\ui\index.html`
- `E:\Cortex\tests\test_crew\test_server.py`
- `E:\Cortex\tests\test_crew\test_detect.py`

## Verify

40 passed (test_server + test_detect): `cd E:\Cortex; $env:PYTHONPATH=E:\Cortex; python -m pytest tests/test_crew/test_server.py tests/test_crew/test_detect.py -q`

## Leftover founder bind

Live `:8020` is still the Cortex-crew fork. YOU step 8: founder restarts `python -m CortexOS.crew` from `E:\Cortex`. Agents do not kill it (R-0015). Engine tree is this seating. Control still probes `:8020`.
