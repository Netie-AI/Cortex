# Crew inspector wakes/queue R-0011 idle != unread

- Date: 2026-09-03
- Keywords: crew, inspector, wakes, queue, HITL, r-0011, unread, stolen.css
- Main idea: Engine Crew inspector names GET /crew/wakes and /crew/belt absence (never green empty). Ok empty JSON says "wakes none" / "queue none" / "HITL 0". One composer. stolen.css stays 410. Live :8020 is still the fork.

## What shipped

- `CortexOS/crew/ui/index.html`: failed or not-yet GETs keep class `empty` and say unread. Ok empty lists drop `empty` and say idle. Catch sentinels are `{ ok: false, unread: true }`, not empty arrays.
- `CortexOS/crew/ui/crew.css`: `#wakes.empty` and `#queue.empty` share the `#engine.empty` note box so unread is not quiet-green.

## Still true

Control stays plane 4. Converse is Crew. Do not POST `/v1/run`. Agents do not restart `:8020` or `:8040` (R-0015). This tree is not the live converse process until founder rebind.
