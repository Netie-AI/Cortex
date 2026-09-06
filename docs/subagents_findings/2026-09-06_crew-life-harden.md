```yaml
keywords: [crew, life, spawn, wait, stop, idle, goal, hud, epic-116]
main_idea: "CREW-LIFE-HARDEN: honest wait/idle/stop chips; wait is not invent-green running; stop does not kill Manager."
models: [grok-4.6]
workflow: crew-life-harden
reuse: 2026-09-06_crew-a2a-harden
status: verified
cite: agent: crew-life-harden
repo: Cortex
date: 2026-09-06
```

# CREW-LIFE-HARDEN spawn/wait/stop/idle honesty

PREFLIGHT: PARTIAL
reuse: 2026-09-06_crew-a2a-harden, c151eb23 life #142, 59ebed31 slash #158
spawn: skip

## Already on origin/main

- `life.py` statuses, `operator_spawn`, `stop_agent`, `kill_agent`, `set_agent_mode`
- Slash `/spawn /kill /stop /idle /wait /goal`
- HTTP spawn/stop/kill/mode/accept
- Goal mode survives chat clear
- Manager stop/kill DENIED

## Gaps closed

- HUD `lifeBusy` treated `waiting` as thinking/live (invent-green running).
- `_busy_names` counted operator-parked waiting as still working.
- `/idle` aliased stop and remapped to `goal` when mode=goal.
- No HTTP `/wait` or `/idle`; HUD had no Wait/Idle.
- Mode toggle sent `goal: ""` and wiped `goal_text`.
- Stop while a task ran left status `active` until CancelledError.

## Landed

- `hud_chip` / `is_busy`: waiting/idle/goal/stopped are not live.
- `wait_agent` / `idle_agent`; stop parks immediately then cancels.
- HTTP POST `.../wait` and `.../idle`. Manager DENIED. No :8020 kill.
- HUD chips `life-*`, Wait/Idle, spawnGoal; mode toggle keeps goal text.
- Parent #116. Control untouched. No CLAIMS.json. Freeze lanes not attached.

## Verify

```
python -m pytest tests/test_crew -q
```
