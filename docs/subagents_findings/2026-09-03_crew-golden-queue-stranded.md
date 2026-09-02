# GOLDEN honesty + belt queue + stranded event + start-script port guard

- Date: 2026-09-03
- Keywords: crew, golden, queue, belt, stranded, night_watch, start-crew, r-0015, control-display
- Main idea: GOLDEN.md now points writers at E:\Cortex\CortexOS\crew. Control paints queue/HITL from belt JSON. Restart fires stranded events. Start scripts do not bind :8020 twice when the fork still holds the port.

## What shipped

- `docs/GOLDEN.md` write-target is engine Crew. Fork is converse-only until founder rebind.
- Control `_crew_belt_body` renders `queue`, `confirms`, `spaces`, `agents` when present. No POST/approve/spawn.
- Crew inspector Queue pane from `GET /crew/belt`.
- `recover_stranded` fires `stranded` / `stranded:{space_id}`.
- `night_watch.ps1` and `Start-CrewApp.ps1` skip a second start when TCP :8020 is held.

## Still true

Live `:8020` is the Cortex-crew fork. Agents must not kill it. `stolen.css` replace is a parallel pass (`crew.css`, no index.html swap yet).
