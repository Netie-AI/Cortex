```yaml
keywords: [crew, grokbot, guaca, rakazo, uacc, a2a, keys]
main_idea: Cortex Crew is a standalone :8020 Guaca-shaped agentic UI on worktree D:\Cortex-crew (claude/crew-agentic-interface). Do not write it into D:\Cortex ANS or kind-euclid.
models: [cursor-grok-4.6]
workflow: 2026-08-23_cortex-crew-standalone
reuse: golden_rule
status: verified
cite: distill: skill_distill/captures/2026-08-23_cursor_cortex-crew.md
repo: Cortex
date: 2026-08-23
```

# Cortex Crew lives on D:\Cortex-crew, not the ANS checkout

## Main idea

Standalone FastAPI on 8020 (`python -m CortexOS.crew`). Provider keys via UI -> `data/crew/keys.json` (gitignored). Computer control is UACC/Windows-MCP/computer-control-mcp behind `CORTEX_COMPUTER_CONTROL=1` + per-server arm + per-call confirm. kind-euclid is mounting a parallel `webui/crew` into the engine; do not merge those trees from this lane.

## Golden rule

> Crew is a sidecar. Never import packs. Never open DuckDB. Never write crew files into `cursor/ans-01-02-03-governed-metric` or `claude/kind-euclid-173eac`.

## Verify

```bash
cd D:\Cortex-crew
$env:PYTHONPATH="D:\Cortex-crew"
D:\Cortex\.venv\Scripts\python.exe -m pytest tests/test_crew -q
# expected: 25 passed
Invoke-WebRequest http://127.0.0.1:8020/crew/health
```
