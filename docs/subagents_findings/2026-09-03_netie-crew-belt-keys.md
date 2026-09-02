# Netie-Crew belt display keys

- Date: 2026-09-03
- Keywords: netie-crew, belt, wakes, queue, converse, 8022, control-display
- Main idea: Seed conveyor GET /v1/belt and GET /crew/belt now carry Control's display keys with honest empties. converse=false. Port stays 8022. Control still probes live :8020. Do not point Control at this host.

## What shipped

- `E:\Netie-Crew\netie_crew\app.py` belt snapshot includes wakes/queue/confirms/spaces/agents plus `/crew/belt` alias.
- POST `/v1/belt` and POST `/crew/belt` are not 200. POST `/v1/wakes` is not 200.

## Still true

Live converse is `:8020`. This process is conveyor-only. Empty wakes/queue until this host has Crew SQLite (it should not grow a second engine).
