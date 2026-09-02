---
keywords: [crew, ingest, analog, ssrf, cgnat, is_global, memory, summarize, github, composer]
main_idea: Remaining analog SSRF was CGNAT/shared 100.64/10 (Alibaba metadata 100.100.100.200). refuse_nonpublic_url now uses not is_global. ingest skips a nonpublic search hit. memory/summarize/github 20s/one composer already at-bar. Live :8020 still the fork.
models: [cursor-grok-4.6]
workflow: 2026-09-03_crew-product-gaps-ingest-cgnat
reuse: golden_rule
status: verified
cite: distill: docs/subagents_findings/2026-09-03_crew-product-gaps-analog-placeholders.md
repo: Cortex
date: 2026-09-03
---

# Crew product gaps: ingest + CGNAT analog (2026-09-03)

PREFLIGHT: HIT - INDEX already covers queue/wakes/mailbox/stall/cancel/fail-closed/redirect/inspector unread/plugins/mcp.

Seated Control GET /v1/contract then pickup/fleet/you/coordinate. Did not POST /v1/run. Did not grow E:\Cortex-crew. Did not restart :8020.

## COPY / DISTILL / SKIP / ALREADY

| Item | Verdict | Path |
|---|---|---|
| analog remaining SSRF | DISTILL. Flag list missed CGNAT/shared. Fixed `not ip.is_global`. | `CortexOS/crew/research.py` `analog.py` |
| ingest.py loopback source | DISTILL. `_pick_source` skipped nonpublic hits. | `CortexOS/crew/ingest.py` |
| memory.py | ALREADY. Jail, caps, wrap recall, prompt_index. Wired. | `CortexOS/crew/memory.py` |
| summarize.py | ALREADY. should_compact + _auto_compact. | `CortexOS/crew/summarize.py` |
| github.py gh timeout | ALREADY. 20s not 60s. desk list_prs uses that. | `CortexOS/crew/github.py` |
| second composer | ALREADY. One `<div class="composer">`. | `CortexOS/crew/ui/index.html` |
| Guaca / Beads / LangGraph | SKIP | -- |

Weird encodings (`127.1`, decimal, hex) do not resolve on this Windows urllib. COPY none.

## Still true

Live `:8020` is the Cortex-crew fork. Do not kill it (R-0015). Control does not POST `/v1/run`.

## Verify

```
cd E:\Cortex
PYTHONPATH=E:\Cortex python -m pytest tests/test_crew/test_ingest.py tests/test_crew/test_memory.py tests/test_crew/test_summarize.py tests/test_crew/test_server.py tests/test_crew/test_analog.py -q
# 64 passed
```
