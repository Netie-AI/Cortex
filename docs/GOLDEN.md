# Golden checkouts (2026-08-24)

One git repo. Many worktrees. Do not merge them into one folder.

| Job | Golden folder | Branch | Do |
|-----|---------------|--------|----|
| Crew chat :8020, skills, night_watch | `D:\Cortex-crew` | `claude/crew-agentic-interface` | Write crew here. Standalone `python -m CortexOS.crew.server` |
| Engine / DMS answer path :8010 | `D:\Cortex` | `cursor/ans-01-02-03-governed-metric` | ANS-01/02/03 PR. Do not copy Crew into this tree |
| Published engine | GitHub `origin/main` | `main` | Merge PRs here. Crew is still unpushed |
| ANS golden pins | `D:\Cortex-ans-golden` / `D:\Cortex-ans04-golden` | pin branches | Benchmark pins. Do not land crew or product features |

## Why Cortex-crew exists

Crew is a plane-4 sidecar. The ANS checkout owns :8010 and a different Cursor lane. Mixing them dual-writes the same files. Cite: `docs/subagents_findings/2026-08-23_crew-isolated-worktree.md`.

`D:\Cortex\CortexOS\crew` is a leftover stub (~21 files, engine-mounted `/crew`). Ignore it. The real Crew is 77+ files under `D:\Cortex-crew\CortexOS\crew`.

## Do not merge

- `cursor/ans-*-golden*` pin branches into crew
- `claude/kind-euclid-*` and other live Claude pads into crew
- Constructor/Hyperlift worktrees into crew (already on `origin/main`; rebase crew onto main instead)
- Crew into `D:\Cortex` governed-metric

## Keep-alive

`NetieEstate24x7` -> `D:\Cortex-crew\scripts\night_watch.ps1 -SkipEstate`. Computer control off.
