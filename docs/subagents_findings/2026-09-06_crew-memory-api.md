```yaml
keywords: [crew, memory, collection, facts.md, scope, user, agent, run, epic-116]
main_idea: "CREW-MEMORY-API: space|user|agent|run collections persist markdown and survive chat clear. HUD uses list/search/export API, not derived chat state."
models: [grok-4.6]
workflow: goal-crew-control
reuse: 2026-09-04_crew-facts-md-hud
status: verified
cite: agent: crew-memory-api
repo: Cortex
date: 2026-09-06
```

# CREW-MEMORY-API collection scopes

PREFLIGHT: PARTIAL
reuse: 2026-09-04_crew-facts-md-hud, crew-belt-claim
spawn: skip

## Gap

Chrome already painted space|user|agent|run chips. Only space hit `/crew/spaces/{id}/memory`. user/agent/run were derived from messages/agents/runStatus and vanished on Clear chat.

## Landed (branch `cursor/crew-memory-api-6ce8`)

- `collection_for(data_dir, space_id, scope=, owner=)` — space stays `memory/`; others `collections/<scope>/<owner>/`
- Defaults: user=operator, agent=crew, run=session
- HTTP: list `GET .../memory?scope=`, search `GET .../memory/search`, save POST, export GET, forget DELETE
- HUD chips/search/save/export call those endpoints
- Recall still `wrap_untrusted_payload`
- Slash remember remains the space writer (no dual-write)

## Verify

```
python -m pytest tests/test_crew -q
```

## Not this slice

Control stays display-only. CLAIMS.json not written. Standing-approval ladder untouched. Live `:8020` needs founder restart. GitHub CI not claimed.
