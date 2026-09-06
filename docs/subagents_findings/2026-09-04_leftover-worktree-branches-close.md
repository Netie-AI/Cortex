```yaml
keywords: [worktree, leftover, eval-01, space-01, c7-design, ci-hygiene, warehouse-path, prune]
main_idea: "Four leftover session branches were already squash-merged; deleted locally. Warehouse call-time DMS_WAREHOUSE_DB is still missing on live main; keep PR #43."
models: [grok-4.6]
workflow: worktree-continue-then-close
reuse: golden_rule
status: verified
cite: agent: leftover-worktree-close
repo: Cortex
date: 2026-09-04
```

# Leftover session branches: four closed, warehouse not

PREFLIGHT: HIT
reuse: `2026-09-03_worktree-branch-cap.md`
spawn: skip

## Verdict

| Session leftover | Local twin | GitHub | Action |
|---|---|---|---|
| claude/eval-01-corpus-gate | cursor/eval-01-corpus-gate | PR #108 MERGED | deleted local |
| claude/space-01-grant | cursor/space-01-grant | PR #107 MERGED | deleted local |
| claude/c7-design-epic-006 | cursor/c7-design-epic-006 | PR #106 MERGED | deleted local |
| claude/ci-hygiene-main-only | cursor/ci-hygiene-main-only | PR #111 MERGED | deleted local |
| claude/warehouse-path-call-time | none (stale PR #43 head cursor/warehouse-path-4abf) | PR #43 OPEN | keep |

`claude/*` names from the cloud session never existed on this laptop or on GitHub. GitHub already deleted the `cursor/*` heads after squash-merge. Issue #97 is already CLOSED.

## Warehouse is not done

Live `main` (`080e47e`, includes #119) still has:

```python
def warehouse_path(db_path=None):
    return Path(db_path or DEFAULT_DB)
```

`DEFAULT_DB` is an import-time snapshot. PR #43 is still the open vehicle and is a mixed 31-file branch, not a narrow call-time port. Local `cursor/scale-wave1-2026-08-27` already re-reads `DMS_WAREHOUSE_DB` in `CortexOS/execution/warehouse.py`; that patch is not on `main`.

## Not pruned this pass

`D:\Cortex-wt\` still holds 7 extra heads. Merged and clean enough to prune later: c7-03 #123, c7-04 #121, rag-03 #118, verify-drafts #114, vq-01 #125. Keep: c7-05 (open PR #126), c7-02 (dirty tree; files already on live main). Cap remains 4 live heads.

## Verify

```
git branch --list cursor/eval-01-corpus-gate cursor/space-01-grant cursor/c7-design-epic-006 cursor/ci-hygiene-main-only
gh pr view 108,107,106,111,43,97 -R Netie-AI/Cortex
```

Empty branch list; 108/107/106/111 merged; 43 open; 97 closed.
