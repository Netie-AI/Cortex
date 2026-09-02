---
keywords: [crew, analog_clone, ssrf, loopback, stolen.css, composer, shell, voice, health, r-0011, unread]
main_idea: analog_clone now refuses loopback/RFC1918 before fetch and does not dump remote HTML into converse. Voice/engine/conn placeholders name unread, not live or checking. stolen.css 410, one composer, shell.py never executes. Live :8020 is still the fork.
models: [cursor-grok-4.6]
workflow: 2026-09-03_crew-product-gaps-analog-placeholders
reuse: golden_rule
status: verified
cite: distill: docs/subagents_findings/2026-08-28_crew-analog-clone-igloo.md
repo: Cortex
date: 2026-09-03
---

# Crew product gaps: analog SSRF + unread placeholders (2026-09-03)

PREFLIGHT: HIT - analog-clone-igloo, stolen-gone, inspector-skills-r0011, crew-health-fail-closed.

## Hunt

| Item | Verdict | Path |
|---|---|---|
| analog.py loopback fetch | COPY none. Fixed refuse_nonpublic_url. Did write+open on 127.0.0.1 | `CortexOS/crew/analog.py` `research.py` |
| analog_clone HTML into converse | ALREADY. Result has no text/html. index.html is original template | `analog.py` `_html` |
| second composer | ALREADY. One `<div class="composer">` | `ui/index.html` |
| stolen.css linked | ALREADY. `/crew.css` live. GET `/stolen.css` 410 | `server.py` |
| voice/health look healthy | Fixed. conn/engine/voice start unread, not live/checking | `ui/index.html` |
| shell.py unjailed exec | ALREADY. Decision layer. No subprocess | `shell.py` |
| Guaca / Beads / LangGraph | SKIP | -- |

## Still true

Live `:8020` is the Cortex-crew fork. Do not kill it (R-0015). Control does not POST `/v1/run`. urllib redirect-to-loopback stays in `execution/web_tools.py` (not this tree).

## Verify

```
cd E:\Cortex
PYTHONPATH=E:\Cortex python -m pytest tests/test_crew/test_server.py tests/test_crew/test_analog.py tests/test_crew/test_shell.py -q
```
