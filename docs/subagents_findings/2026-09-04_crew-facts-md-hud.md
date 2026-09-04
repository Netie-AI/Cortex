```yaml
keywords: [crew, facts.md, memory, epic-116, hud, clear-chat]
main_idea: "EPIC-CREW-01 leftover was facts.md not on main. LIFE/CMD/ROUTER/RUNTIME already merged. Landed durable markdown memory on cursor/crew-facts-md-116."
models: [grok-4.6]
workflow: goal-crew-control
reuse: golden_rule
status: verified
cite: agent: crew-facts-md-116
repo: Cortex
date: 2026-09-04
```

# Crew facts.md on CortexOS/crew

PREFLIGHT: HIT
reuse: analog-map-plans-hub, crew-isolated-worktree, 2026-09-04_crew-rakazo-memory-ux
spawn: skip

## Gap

Epic #116 children #115/#129/#130/#131/#133/#135 are CLOSED. Remaining acceptance: memory list/save/search/export markdown + facts.md surviving chat clear, painted on the HUD.

## Landed (uncommitted, branch `cursor/crew-facts-md-116` from github/main)

- `CortexOS/crew/workspace.py` jail
- `CortexOS/crew/memory.py` named notes + rebuilt `facts.md`
- HTTP GET/POST `/crew/spaces/{id}/memory` + `/memory/export`
- `/remember` `/recall` `/forget` `/memory` slashes
- HUD Save / Export / search; space scope reads the API not skill titles
- Clear chat does not delete the memory dir

## Verify

```
D:\Cortex\.venv\Scripts\python.exe -m pytest tests/test_crew -q
```

## Not this slice

Control stays display-only (405 `/v1/goal`). Completing Netie tickets stays Ticket Runner + seated writers. API keys / forever-run bench wait on the founder.
