```yaml
keywords: [crew, a2a, ask-card, deadlock, switchboard, wait_for_replies, epic-116]
main_idea: "CREW-A2A-HARDEN: walk wait cycles, refuse wait-while-owed, paint ask cards from Switchboard HUD. No invented replies."
models: [grok-4.6]
workflow: crew-a2a-harden
reuse: 2026-08-23_crew-agentic-interface
status: verified
cite: agent: crew-a2a-harden
repo: Cortex
date: 2026-09-06
```

# CREW-A2A-HARDEN Switchboard ask cards

PREFLIGHT: PARTIAL
reuse: 2026-08-23_crew-agentic-interface, 2026-09-06_crew-memory-api
spawn: skip

## Gap

CREW-A2A-UX (#135 / PR #144) correlated ask/reply and fail-loud dead targets.
Chrome still regrouped locally (ignored `hud.threads`), `msg--ask` had no CSS,
`would_deadlock` only caught a 2-cycle, and `wait_for_replies` could burn a
timeout while a teammate was blocked waiting on you.

Live `:8020` was not listening in this VM; chrome was read from
`CortexOS/crew/ui` plus TestClient GET `/` and `/crew.css`.

## Landed

- `CortexOS/crew/a2a.py` — wait-graph deadlock; `pending_on`; HUD waiting/dead/timeout.
- `runtime.py` — refuse ask-back and wait-while-owed; emit a2a waiting on arm.
- Chrome ask cards + stall chips from GET `/spaces/{id}/threads`.
- Parent #116. Control untouched. No CLAIMS.json. Freeze lanes not attached.

## Verify

```
python -m pytest tests/test_crew -q
```

237 passed locally. Not a GitHub CI claim.
