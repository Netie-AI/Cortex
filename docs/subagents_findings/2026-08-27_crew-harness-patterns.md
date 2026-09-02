---
keywords: [crew, harness, deepagents, openwork, grok-bot, workspace, todos, compact, license]
main_idea: License stripping and grok-bot vendoring refused. Crew gained original jailed workspace, write_todos, load_skill, compact. Patterns from MIT deepagents/openwork-core already vendored at E:\Netie-Crew. No second DAG.
models: [cursor-grok-4.6]
workflow: 2026-08-27_crew-harness-patterns
reuse: golden_rule
status: verified
cite: distill: E:\Netie\Internal\Workflow\ECOSYSTEM_EXECUTION_PLAN.md#5
repo: Cortex
date: 2026-08-27
---

# Crew harness patterns (2026-08-27)

PREFLIGHT: HIT - analog nearness, stolen UI, isolated worktree, Netie-Crew vendor already on disk.

## Main idea

Do not strip licenses or clone `b-nnett/grok-bot-0.18-reconstructed` into Cortex.
`E:\Netie\Guaca` is missing. MIT vendors already live at `E:\Netie-Crew\vendor\deepagents`
and `E:\Netie-Crew\vendor\openwork` (`ee/` stripped). Cortex crew now has original
jailed workspace, todos, load_skill, and compact. dag_runner stays the decision layer.

## Golden rule

> Clone is a license decision. Absorb MIT patterns into original code. Zero grok-bot bytes.

## Verify

```bash
python -m pytest tests/test_crew -q
```
