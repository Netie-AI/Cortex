---
keywords: [crew, belt_payload, worktree, identity, HITL, PEER_HEALTH_WAIT_S, tas-crew]
main_idea: Five TAS-CREW engine rules were already shipped. Missing tests pinned belt queue/confirms, PEER_HEALTH_WAIT_S=1.0, spawn-continues-when-worktree-unset, user identity stamp. No crew production code. Live :8020 is not the engine.
models: [cursor-grok-4.6]
workflow: 2026-09-03_crew-tas-gaps-already
reuse: golden_rule
status: verified
cite: distill: E:\Netie\TAS\TAS-CREW.md
repo: Cortex
date: 2026-09-03
---

# TAS-CREW engine hunt: already at-bar, tests filled

PREFLIGHT: HIT - INDEX already covers queue/wakes/mailbox/stall/cancel/fail-closed/start_crew no-double-bind.

Seated Control GET /v1/contract then pickup/fleet/you/coordinate. Did not POST /v1/run. Did not grow E:\Cortex-crew. Did not restart :8020.

## What was missing vs already at-bar

| Rule | Engine tree | Gap this pass |
|---|---|---|
| belt_payload wakes/queue/confirms | `server.CrewApp.belt_payload` already passes all three | HTTP test only pinned wakes. Added queue+confirms on GET /crew/belt and /v1/belt |
| worktree optional spawn | `worktree.attach_worktree` skips if CREW_WORKTREE_ROOT unset; `runtime._spawn` continues | Module tests existed. Added spawn_agent(worktree=true) continues with empty path |
| identity stamp | `store.add_message` stamps meta.space/role/name | Assistant+agent already asserted. Pinned user stamp too |
| HITL floors | `policy.py` + `approvals.py` cannot-loosen; mutating confirm; takeover | Already green. No new test |
| PEER_HEALTH_WAIT_S=1.0 | `server.PEER_HEALTH_WAIT_S = 1.0`; hang test < 2.5s | Hang test would still pass at 2.0s. Pinned the constant = 1.0 |

No production writes under `E:\Cortex\CortexOS\crew`. Live `:8020` is still the fork until founder restart. Engine sidecar `:8023` is a different process.

Verify (73 passed): `python -m pytest tests/test_crew/test_wakes.py tests/test_crew/test_queue.py tests/test_crew/test_queue_wire.py tests/test_crew/test_store.py tests/test_crew/test_server.py tests/test_crew/test_runtime.py -q`

## COPY / DISTILL / SKIP / ALREADY

| Verdict | Path |
|---|---|
| ALREADY | `E:\Cortex\CortexOS\crew\server.py` belt_payload + PEER_HEALTH_WAIT_S=1.0 |
| ALREADY | `E:\Cortex\CortexOS\crew\worktree.py` + `runtime._spawn` worktree=true continues if unset |
| ALREADY | `E:\Cortex\CortexOS\crew\store.py` identity stamp on every add_message |
| ALREADY | `E:\Cortex\CortexOS\crew\policy.py` `approvals.py` HITL floors |
| ALREADY | `queue.py` `wakes.py` `stall.py` `a2a_cursors` `_prime_mailbox` -- do not rebuild |
| DISTILL | `E:\Netie\mygastown\internal\checkpoint\checkpoint.go` pattern only (no root LICENSE) |
| DISTILL | `...\internal\daemon\convoy_manager.go` stranded scan (pattern only) |
| DISTILL | `...\internal\convoy\operations.go` feedNextReady (pattern only) |
| DISTILL | `...\internal\beads\beads_queue.go` claim (pattern only; GitHub Issues stay SoT) |
| DISTILL | `...\internal\keepalive\keepalive.go` stale lease (pattern only) |
| DISTILL | `E:\myopenworker\coworker\selfwake.py` Wake schema (no LICENSE on clone) |
| SKIP | Beads / `bd` as bus; LangGraph; second dag_runner; Guaca AGPL; Control converse; Control POST wakes; grow `E:\Cortex-crew`; restart hung `:8020` |
| COPY | none (Gas Town root LICENSE missing; OpenWorker LICENSE missing) |

## Still planned (not this pass)

1. Extract fleet/roles into `E:\Netie-Crew` then retire the fork.
2. Founder restart so live `:8020` is this engine tree. `start_crew.ps1` already refuses a second bind.

## Still true

Control stays plane 4. Four 405s. Converse stays `:8020`. Agents do not start or kill the hung fork (R-0015).
