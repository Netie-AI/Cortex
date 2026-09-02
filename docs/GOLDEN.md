# Golden checkouts (2026-09-03)

One git repo. Many worktrees. Do not merge them into one folder.

| Job | Golden folder | Do |
|-----|---------------|----|
| Crew converse `:8020` | `E:\Cortex\CortexOS\crew` (`python -m CortexOS.crew`) | Write here. `queue.py` / `wakes.py` wired. Do not grow `E:\Cortex-crew`. |
| Live process today | may still be the `E:\Cortex-crew` fork | Founder rebind (Control YOU step 8). Agents do not kill `:8020` (R-0015). |
| Seed conveyor `:8022` | `E:\Netie-Crew` | GitHub belt only. Not a second converse. |
| Engine / DMS `:8010` | `E:\Cortex` (or `D:\Cortex` ANS) | Cortex decides work shape. Crew talks over HTTP. |
| Published engine | GitHub `origin/main` | Merge PRs here. |

## Why a fork existed

Crew used to live in an isolated worktree so ANS `:8010` and Crew `:8020` did not dual-write. Cite: `docs/subagents_findings/2026-08-23_crew-isolated-worktree.md`.

That split is retired as a write-target. Lawful Crew code is `E:\Cortex\CortexOS\crew`. `E:\Cortex-crew` is converse-only until the founder stops it and starts `scripts\start_crew.ps1`.

`D:\Cortex\CortexOS\crew` as "leftover stub" is stale. Do not ignore the engine tree.

## Do not merge

- `cursor/ans-*-golden*` pin branches into crew
- Analog clones into crew (Guaca AGPL SKIP)
- Crew converse into Control (`E:\NetieControl`). Control stays plane 4. Four 405s.

## Keep-alive

`NetieEstate24x7` -> `E:\Cortex\scripts\night_watch.ps1 -SkipEstate`. If `:8020` is held but `/crew/health` unread, do not start a second process. Engine-tree `/crew/health` fail-closes peer pings in 1s; the live fork may still hang until founder rebind.
