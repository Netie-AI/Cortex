---
keywords: [crew, harness, deepagents, openworker, optio, approvals, memory, queue, compact, dispatch, shell, skills, seeding, billing]
main_idea: Roster seeding leaked into the run path and broke broadcast. Seven harness gap modules landed as original code from MIT deepagents/openworker patterns; queue and shell deliberately left un-wired. Actions billing still blocks every check, so no PR can go green.
models: [claude-opus-5]
workflow: 2026-08-28_crew-harness-gap-wave
reuse: golden_rule
status: verified
cite: distill: docs/subagents_findings/2026-08-27_crew-harness-patterns.md
repo: Cortex
date: 2026-08-28
---

# Crew harness gap wave (2026-08-28)

PREFLIGHT: HIT - crew-harness-patterns, ecosystem-analog-nearness, crew-control-app-ui,
overnight-watchdog-tickets.

## Main idea

`ensure_roster` was called from `on_user_message`, so every run seeded ten idle
specialists. `broadcast` then fanned out to teammates the Manager never spawned and
"do not spawn agents" still produced a full desk. Seeding is desk display: the run
path uses `ensure_manager`, the server seeds when it renders a space.

Seven modules absorbed the remaining deepagents/openworker harness gaps as original
code - `approvals`, `memory`, `queue`, `summarize`, `dispatch`, `shell`, `skill_index`.
Two are landed and tested but deliberately NOT on the hot path: `queue` would put a
second source of truth about run state beside the `runs` table and needs blocking file
I/O on the asyncio loop; `shell` is a gate with no executor, and `run_command` is
deliberately absent from `policy.INTERNAL_TOOLS` so the name refuses.

`auto_tools` exists in `approvals` but is not a `spawn_agent` parameter: it only ever
keeps an ALLOW an ALLOW, so offering the model a knob by that name would advertise a
bypass that does not exist.

## Golden rule

> Seeding is display, never the run path. An agent grant may only tighten `policy.decide`,
> never loosen it. A landed, tested, un-wired module beats dead code on the run path.

## Verify

```bash
python -m pytest tests/test_crew -q       # 349 passed
ruff check CortexOS packages/cortex_contract scripts tests/packaging tests/contract
lint-imports                              # 2 kept, 0 broken
python scripts/check_versions.py
```

`tests/dms/` errors with `Cannot open file "data\dms_demo.duckdb"` are a running estate
process holding the file, not code. `scripts/export_openapi.py --check` exits 0 while
*skipping* without `.[full]` installed - that is a gate that cannot fail, so it does not
count as passed. GitHub Actions on Netie-AI/Cortex still fails every job in 2-4s with
`steps: 0` (billing), so no PR can be green regardless of code.
