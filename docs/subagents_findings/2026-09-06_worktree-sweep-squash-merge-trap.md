# Worktree sweep -- the squash-merge trap and 9k unlanded lines

- Date: 2026-09-06
- Keywords: worktree, prune, squash-merge, git-cherry, dubious-ownership, dangling-blob, salvage, r-0003, r-0007, r-0008
- Main idea: A squash-merged branch defeats every cheap "is it merged" signal. Only per-file
  branch-adds against origin/main is sound. Under that test, 10 of 15 worktrees were provably
  landed and prunable, while the PRIMARY checkout held 147 files / 9132 pure-added lines that
  exist in no commit, no branch, and no remote.

## Root cause class

`git branch --merged`, `git cherry`, and `git rev-list --count` all fail on squash-merges:
the squash rewrites the patch-id and never becomes an ancestor. `git diff origin/main <branch>`
is separately polluted by main moving ahead. A stale branch also re-adds OLD versions of files
main has since changed, so a nonzero `+` count does NOT prove unlanded work.

Sound test: for each file the branch touched relative to the merge base, count branch-adds and
branch-removes. adds ~0 with removes > 0 means main is ahead. Only files with pure adds and no
removes are candidates, and each must still be grepped for across ALL of main because work
routinely lands in a different module (C7-03 plausibility moved to `CortexOS/dms/l2_plausibility.py`).

## Two traps that silently report success

1. **Dubious ownership.** `git -C D:/Cortex-wt/<x> status --porcelain` dies with
   "detected dubious ownership" and prints NOTHING. A `wc -l` on that output reports 0 and reads
   as CLEAN. Three dirty worktrees were falsely reported clean this way before the check was
   redone with `git -c safe.directory=<path>`. Always grep the output for `fatal`.
2. **PR #73 covered one commit of four.** The branch showed a merged PR, but #73 merged
   2026-08-27 23:13 and squashed only `e09efa3` (authored 22:52). The other three commits were
   authored 08-28, 09-03 and 09-04 -- after the merge -- so they could not be in it. Proven by
   timestamp, not inference.

## R-0007 applied to grep

Before trusting "symbol absent from main means unlanded", the verifier proved the grep could
find things: `canonical_manifest_bytes` -> 26 hits, `list_prs` -> 5, `_public_run` -> 1, a bogus
token -> 0. Only then were the ten zero-hit symbols read as real absences.

## What was actually at risk

- `CortexOS/execution/scheduled_work.py` + its test, 456 lines, in NO git object. Re-landed as
  PR #183 (merged, main d360441) after adapting `GH_WAIT_S` to main's `_gh_wait_s()` and adding
  `github.pr_diff` / `create_pr`, without which the module raises AttributeError at run time.
- 147 files / 9132 pure-added lines on `cursor/scale-wave1-2026-08-27`, including 11 crew modules
  (dispatch 384, skill_index 360, summarize 270, analog 252, approvals 237) and 10 crew tests.
  `git branch -r --contains 161b017` is EMPTY: no remote carries any of it.
- A DANGLING blob `3d418911` holding the `packs/dms/semantic/values.py` delta, readable via
  `git cat-file -p` but destroyed by any `git gc`.

## Verify

```
git worktree list                       # 10, was 17
git bundle verify D:\Cortex-salvage\2026-09-06\scale-wave1-unlanded.bundle
git branch --merged origin/main         # ancestor-merged only; squash-merged never appear
```

Salvage: `D:\Cortex-salvage\2026-09-05`, `D:\Cortex-salvage\2026-09-06`, mirrored off-disk to
`C:\Users\OoiJianHong\cortex-salvage-mirror`.

## Still true

Agents do not start or kill `:8020` (R-0015). `:8020` died on its own mid-session; `:8023` is the
hung fork. A second lane creates worktrees continuously -- `engine-8010` and `rsf-04` appeared
during the sweep. RSF-01/02/03 landed on main as #180/#181/#182 despite the do-not-seat hold.
