---
keywords: [crew, analog, ssrf, ipv6, siit, nat64, ipv4-compatible, localhost6, research]
main_idea: analog/research refused inet_aton and ipv4-mapped, but IPv4-compatible ::127.0.0.1, SIIT ::ffff:0:127.0.0.1, and NAT64 64:ff9b::127.0.0.1 still looked public. Host aliases localhost6/ip6-localhost/ip6-loopback/local/broadcasthost also allowed. DNS-rebinding stays parked. Live :8020 still fork.
models: [cursor-grok-4.6]
workflow: 2026-09-03_crew-analog-ipv6-embed
reuse: golden_rule
status: verified
cite: distill: docs/subagents_findings/2026-09-03_crew-transcript-apiget-r0011.md
repo: Cortex
date: 2026-09-03
---

# Crew analog IPv6-embed leftover (2026-09-03)

PREFLIGHT: HIT - INDEX already covers analog 127.1 / octal / mapped IPv6 / file / ftp / instance-data / .local / CGNAT / analog_clone no write on fail. DNS-rebinding names (localtest.me) are PARKED -- do not add DNS.

Seated write-target `E:\Cortex\CortexOS\crew`. Did not grow `E:\Cortex-crew`. Did not restart `:8020` or `:8023`. Did not rebuild queue.py / wakes.py / stall.py. Did not add DNS.

## COPY / DISTILL / SKIP / ALREADY

| Item | Verdict | Path |
|---|---|---|
| IPv4-compatible `::127.0.0.1` / `::7f00:1` / `::10.0.0.1` | DISTILL. `is_global` true; Python 3.13 dropped `ipv4_compatible`. Now extract last 32 bits from `::/96`. Public `::8.8.8.8` still allowed. | `research.py` |
| SIIT `::ffff:0:127.0.0.1` | DISTILL. Not `ipv4_mapped` (`::ffff:a.b.c.d`). Prefix `::ffff:0:0:0/96`. | `research.py` |
| NAT64 well-known `64:ff9b::127.0.0.1` | DISTILL. `64:ff9b::/96`. Public `64:ff9b::8.8.8.8` still allowed. | `research.py` |
| localhost6 / ip6-localhost / ip6-loopback / local / broadcasthost | DISTILL. Same class as `localhost` / `.local`. Hostname denylist, not DNS. | `research.py` |
| localtest.me / nip.io | ALREADY parked. Still None. Do not add DNS. | `research.py` |
| 127.1 / mapped `::ffff:7f00:1` / file / ftp / CGNAT / .local / instance-data | ALREADY. Probe still refuses. | `research.py` |
| queue.py wakes.py stall.py | ALREADY. Do not rebuild. | -- |
| Guaca / Beads / LangGraph | SKIP | -- |
| COPY gastown/openworker bytes | SKIP. LICENSE missing. | -- |

## Files

- `E:\Cortex\CortexOS\crew\research.py`
- `E:\Cortex\tests\test_crew\test_research.py`
- `E:\Cortex\tests\test_crew\test_analog.py`

## Verify

13 passed: `cd E:\Cortex; $env:PYTHONPATH=E:\Cortex; python -m pytest tests/test_crew/test_research.py tests/test_crew/test_analog.py -q`

## Leftover founder bind

Live `:8020` is still the Cortex-crew fork. YOU step 8: founder restarts `python -m CortexOS.crew` from `E:\Cortex`. Agents do not kill it (R-0015). Control still probes `:8020`.
