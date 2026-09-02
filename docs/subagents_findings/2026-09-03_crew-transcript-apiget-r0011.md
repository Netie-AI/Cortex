---
keywords: [crew, transcript, messages, r-0011, unread, apiGet, analog, first-paint]
main_idea: First-paint log names transcript unread vs messages none. selectSpace/catchUp GETs use apiGet 2.5s. Analog leftover besides DNS already refused. Live :8020 still fork.
models: [cursor-grok-4.6]
workflow: 2026-09-03_crew-transcript-apiget-r0011
reuse: golden_rule
status: verified
cite: distill: docs/subagents_findings/2026-09-03_crew-import-detect-computer-r0011.md
repo: Cortex
date: 2026-09-03
---

# Crew transcript first-paint + apiGet (2026-09-03)

PREFLIGHT: HIT - INDEX already covers import/detect/computer unread, analog 127.1, providers skip vault, belt skip engine, keys/host/search unread, engine/voice unread, desk GH 1.5s, roles/plugins/mcp/wakes/queue unread, stolen.css 410, CGNAT/.local, resolve_providers no healthz, PEER_HEALTH 1s, analog loopback, no loading...

Seated write-target `E:\Cortex\CortexOS\crew`. Did not grow `E:\Cortex-crew`. Did not restart `:8020` or `:8023`. Did not rebuild queue.py / wakes.py / stall.py. Did not add DNS.

## COPY / DISTILL / SKIP / ALREADY

| Item | Verdict | Path |
|---|---|---|
| GET messages first-paint blank log | DISTILL. Idle-empty log looked like a quiet transcript. First-paint unread. Failed GET unread. Idle success is messages none. | `ui/index.html` `ui/crew.css` |
| selectSpace / catchUp unbounded `api()` vs apiGet 2.5s | DISTILL. Space GETs (messages/agents/confirms/todos/wakes) now apiGet. POST send still `api()`. estate_status stays chat-driven. | `ui/index.html` |
| remaining first-paint GET 404 | ALREADY. Boot paths exist. `/stolen.css` 410. `/favicon.ico` 204. Voice/search are modal GETs with unread. | `server.py` |
| sequential HTTP besides estate_status | ALREADY. health/desk/connectors/GH/IMAP/PEER_HEALTH parallel and under 2.5s. Tickets local. Providers skip vault. Belt skips engine. | `server.py` `desk.py` `connectors.py` `github.py` |
| analog leftover besides parked DNS-rebinding | ALREADY. Probe: octal/zero-pad/dotted-hex inet_aton, IPv4-mapped `::ffff:7f00:1`, ULA/link-local/docs IPv6, file/ftp, 0.0.0.0, broadcast, userinfo-to-loopback all refuse. localtest.me / nip.io still None. Do not add DNS. | `research.py` `tests/test_crew/test_research.py` |
| queue.py wakes.py stall.py | ALREADY. Do not rebuild. | -- |
| Guaca / Beads / LangGraph | SKIP | -- |
| COPY gastown/openworker bytes | SKIP. LICENSE missing. | -- |

## Files

- `E:\Cortex\CortexOS\crew\ui\index.html`
- `E:\Cortex\CortexOS\crew\ui\crew.css`
- `E:\Cortex\tests\test_crew\test_server.py`

## Verify

15 passed: `cd E:\Cortex; $env:PYTHONPATH=E:\Cortex; python -m pytest tests/test_crew/test_server.py -q`

## Leftover founder bind

Live `:8020` is still the Cortex-crew fork. YOU step 8: founder restarts `python -m CortexOS.crew` from `E:\Cortex`. Agents do not kill it (R-0015). Control still probes `:8020`.
