# Crew inspector wakes + stall event

- Date: 2026-09-03
- Keywords: crew, wakes, inspector, stall, fire_event, control-you, r-0015
- Main idea: Engine Crew UI lists GET /crew/wakes in the inspector (no Guaca paste). Stall cut fires stall / stall:{space_id}. Control YOU step 8 is the founder rebind of live :8020. Do not restart the hung fork from an agent.

## What shipped

- `CortexOS/crew/ui/index.html` fetches `/crew/wakes` on boot and reconnect. Empty copy: Crew owns the tick. Control does not POST wakes.
- `server.fire_stalls` after a teammate cut calls `wakes.fire_event("stall")` and `stall:{space_id}`. Manager still never cut.
- Control `HITL_STEPS` id `crew-engine-bind` (n=8). `/crew/health` timeout is `CREW_BELT_WAIT_S` (1.5s).

## What is still true

Live converse `:8020` is the Cortex-crew fork until the founder starts `python -m CortexOS.crew` from `E:\Cortex`. Agents must not start or kill that process (R-0015). `stolen.css` stays AGPL-tainted replace-before-merge.
