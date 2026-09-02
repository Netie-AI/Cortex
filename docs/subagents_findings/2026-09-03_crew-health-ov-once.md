---
keywords: [crew, health, openvault, resolve_providers, r-0011, GH_WAIT_S]
main_idea: resolve_providers does not healthz. GET /crew/health pings OV once. GH/IMAP 1.5s. Live :8020 still fork.
models: [cursor-grok-4.6]
workflow: 2026-09-03_crew-health-ov-once
reuse: golden_rule
status: verified
cite: distill: docs/subagents_findings/2026-09-03_crew-engine-voice-unread.md
repo: Cortex
date: 2026-09-03
---

# Crew health OV once + GH 1.5s (2026-09-03)

PREFLIGHT: HIT - INDEX already covers engine unread, desk GH 2s, talk pool.

Seated Control GET /v1/contract then pickup/fleet/you/coordinate. Did not POST /v1/run. Did not grow E:\Cortex-crew. Did not restart :8020 or :8040.

## COPY / DISTILL / SKIP / ALREADY

| Item | Verdict | Path |
|---|---|---|
| resolve_providers healthz | DISTILL. Chain listing no longer pings OV. | `config.py` |
| health() double ping | DISTILL. One gather healthz. | `server.py` (already gather; chain was the extra) |
| GH_WAIT_S 2.0 vs apiGet 2.5 | DISTILL. Cap 1.5. IMAP 1.5. | `github.py` `inbox.py` |
| engine unread vs offline | ALREADY | `ui/index.html` |
| Control talk+health pool, cortex 3-probe pool | ALREADY | `netie_control/app.py` `sources.py` |
| queue.py wakes.py stall.py | ALREADY | -- |
| Guaca / Beads / LangGraph | SKIP | -- |
| Live :8020 fork | NEEDS-YOU YOU step 8 | -- |
