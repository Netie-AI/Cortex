---
keywords: [crew, grok-bot, deepagents, openworker, prompts, orchestration, memory, load_skill, progressive-disclosure]
main_idea: Cortex Crew is closer on harness primitives than as a grok-bot/OpenWork/DeepAgents product. Zero grok-bot bytes. Live Manager injects the memory index and inlines matching skill playbooks this turn.
models: [cursor-grok-4.6]
workflow: 2026-08-28_crew-analog-prompt-wire
reuse: golden_rule
status: verified
cite: distill: docs/subagents_findings/2026-08-28_crew-harness-gap-wave.md
repo: Cortex
date: 2026-08-28
---

# Crew analog prompts wired into the live loop (2026-08-28)

PREFLIGHT: HIT - crew-harness-gap-wave, crew-harness-patterns, ecosystem-analog-nearness,
crew-control-app-ui.

## Main idea

Grok-bot reconstructed is distill-only (no checkout, no license). OpenWork and
DeepAgents are MIT pattern sources already vendored at `E:\Netie-Crew\vendor`.
Crew already had jailed workspace, todos, load_skill, compact, remember/recall,
plan_waves, A2A spawn. The live gap was that those internals sat behind tools
the model might never call. The Manager now sees the memory index every turn
(names + one-liners, bodies still via recall, wrapped untrusted). Matching
skill playbooks are inlined into that same turn (capped); load_skill is only
for truncated or missing files. GET /crew/detect returns skills + load_skill_now.
Teammates get the catalog. Queue and shell stay un-wired on purpose.

## Closeness (evidence, not a percentage)

| Analog | Law | What Crew has | Verdict |
|---|---|---|---|
| grok-bot reconstructed | Zero code, zero assets | UI chips, :8020 chat, tone.md ASCII default, specialist spawn, `/skillname` slash invoke | closer on desk feel; far on their agent loop (cannot copy) |
| DeepAgents | Absorb MIT, no second dag_runner | spawn + A2A, inlined matching playbooks, parallel independent tool_calls, memory index, compact, HITL | closer on harness; far as a coding-agent product |
| OpenWork | MIT core only, never ee/ | skill roster + inlined match (search then read), memory catalog then recall | closer on disclosure; far as a multiplayer org product |

## Golden rule

> Catalog on the prompt. Matching playbook inlined this turn. Never vendor grok-bot.

## Verify

```bash
python -m pytest tests/test_crew -q
# 356 passed (2026-08-28)
```
