```yaml
keywords: [worktree, branch-cap, prune, constructor, babel, scale-wave1]
main_idea: "Five dead worktrees were prunable. Unique leftover was four Constructor/babel tips; other local branches were already on writer HEAD. Cap is 4 live heads."
models: [grok-4.6]
workflow: worktree-continue-then-close
reuse: golden_rule
status: raw
cite: agent: 84b38740-331a-4137-80b6-820f2a5b1b3c
repo: Cortex
date: 2026-09-03
```

# Worktree continue then close, then cap

PREFLIGHT: HIT
reuse: crew-isolated-worktree, crew-golden-checkouts, overnight-watchdog-tickets (scale = seat existing writers)
spawn: smaller-executor (3), not one agent per branch

## Main idea

- `git worktree list` had 5 prunable E:/Temp paths. `git worktree prune` left only `D:/Cortex` on `cursor/scale-wave1-2026-08-27`.
- Cherry-equivalent / content-already-on-HEAD branches closed locally (ANS pins, FF-03, DOC-01, Hyperlift/OV constructor copy, META-01 commit).
- Landed on writer WT then closed: enhance DOCUMENT_REF, session JSON 400, `run_dag` 45s, `@babel/runtime`.
- Parked from `f598efa`: `workspace.*` fetch + `warehouse_path` CSV load. `constructor_app.py` already matched HEAD. Do not re-land crew tests.
- Next spawn: max 4 live heads (`main` + writer + 2). Do not `best-of-n-runner` a new worktree per ticket.

## Golden rule

> Continue unique leftover onto the seated writer. Close the branch when HEAD already has the patch. Cap concurrent heads at 4.

## Verify

```
git worktree list
git branch --list
```
