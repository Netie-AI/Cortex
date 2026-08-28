---
keywords: [crew, grokbot, guaca, a2a, uacc, windows-mcp, rakazo]
main_idea: Cortex Crew is a standalone :8020 agentic UI (spaces, Manager spawn, A2A, MCP behind CORTEX_COMPUTER_CONTROL) that must not be built on the ANS branch or in kind-euclid's engine-mount worktree.
models: [cursor-grok-4.6]
workflow: 2026-08-23_crew-agentic-interface
reuse: golden_rule
status: verified
cite: none
repo: Cortex
date: 2026-08-23
---

# Cortex Crew lives on D:\Cortex-crew, not D:\Cortex

## Main idea

- Worktree `D:\Cortex-crew` branch `claude/crew-agentic-interface`.
- Do not write crew into `D:\Cortex` (ANS-01-02-03) or `kind-euclid-173eac` (they mount `/crew` into the engine).
- Standalone `python -m CortexOS.crew` on :8020; AirGPT mounts `build_router`.
- Computer control: catalogue UACC + Windows-MCP + computer-control-mcp, all disarmed, master switch `CORTEX_COMPUTER_CONTROL=1`.
- LLM: first configured of CREW_MODEL / Anthropic / Cursor / OpenRouter / xAI / DeepSeek / OpenAI-compat / optional Ollama. No silent fallback.

## Keywords (search)

`crew`, `8020`, `spawn_agent`, `uacc`, `windows-mcp`, `kind-euclid`

## Golden rule (if reusable)

> Crew is an isolated worktree + standalone port. Never merge it onto an ANS/answer-plane branch, never import packs.*, never start desktop MCP without the master switch + arm + per-call confirm.

## Verify

```bash
cd D:\Cortex-crew
D:\Cortex\.venv\Scripts\python.exe -m pytest tests/test_crew -q
# expected: 22 passed
D:\Cortex\.venv\Scripts\python.exe -m CortexOS.crew --port 8020
# GET http://127.0.0.1:8020/crew/health -> ok
```
