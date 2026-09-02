---
keywords: [crew, gastown, queue, convoy, keepalive, openworker, deepagents, license]
main_idea: queue.py landed unwired on purpose. Distill gastown checkpoint/stranded/claim/keepalive into original wiring. COPY none until Gas Town LICENSE is on disk. Skip Mayor, beads SoT, LangGraph, ee/, grok-bot.
models: [cursor-grok-4.6]
workflow: 2026-08-28_crew-analog-copy
reuse: golden_rule
status: verified
cite: distill: E:\Netie\TAS\TAS-CREW.md
repo: Cortex
date: 2026-08-28
---

# Crew analog COPY inventory (2026-08-28)

PREFLIGHT: HIT - crew-harness-gap-wave, crew-analog-prompt-wire, crew-harness-patterns.

## Main idea

Wire `queue.py` as original code. Distill Gas Town persistence DNA. Do not rebuild harness/UI/conveyor.

## COPY / DISTILL / SKIP / ALREADY (short)

COPY: none (Gas Town clone has no root LICENSE; myopenworker has no LICENSE).

DISTILL into `queue.py` wiring (top 5):
1. Crash resume -- `E:\Netie\mygastown\internal\checkpoint\checkpoint.go`
2. Stranded reclaim -- `...\internal\daemon\convoy_manager.go`
3. Event feed next -- `...\internal\convoy\operations.go` (`feedNextReadyIssue`)
4. Claim + concurrency -- `...\internal\beads\beads_queue.go`
5. Stale-holder lease -- `...\internal\keepalive\keepalive.go`

Also DISTILL: OpenWorker session grants survive restart (`coworker/sessions.py`); HITL check-in tighten-only (`unattended.py` -> existing `approvals.py`).

SKIP: Mayor, `.beads` as SoT, gastown hooks, OpenWorker desktop, OpenWorker shell executor, LangGraph checkpointer, grok-bot, more Guaca/Rakazo CSS.

ALREADY: queue.py/shell.py modules, a2a+store, approvals tighten-only, memory+playbook inject, workspace/todos/load_skill/compact/detect+spawn, Netie-Crew belt conveyor, stolen.css, vendor deepagents+openwork MIT.

## License traps

- grok-bot: no license -- PIN only
- OpenWork `ee/`: not MIT -- keep stripped
- Gas Town: README says MIT; this clone missing LICENSE -- verify before any COPY
- `E:\myopenworker`: no LICENSE on disk -- pattern-only until cleared
- deepagents MIT OK; refuse LangGraph as Crew brain
- Never strip NOTICE/LICENSE from `E:\Netie-Crew\vendor\*`

Wire target: `runtime._runs` / `_space_run` (in-memory) -> `queue.py` JSONL leases. GitHub Issues stay SoT. `dag_runner` stays decision layer.
