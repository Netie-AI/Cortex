# Cortex - agent index

Read this first. Engine invariants live in `CLAUDE.md`; current state in `STATUS.md`;
parked work in `PARKING_LOT.md` (do not build from it). Live work is GitHub issues.

## Where things are

| What | Where |
|---|---|
| Tests | `tests/` - `tests/contract/` and `tests/invariants/` are protected (see `CLAUDE.md` section 2); hostile SQL corpus is `tests/test_execution/` |
| Verify scripts | `scripts/verify/` - not on `main` yet; until it lands, `scripts/verify_all.ps1` plus the commands below |
| Contract | `contract/` (frozen specs, `compat.yaml`, `testvectors/`); compat check `scripts/check_contract_compat.py`; spec export `scripts/export_openapi.py --check`; versions `scripts/check_versions.py` |
| Import boundaries | `.importlinter` (module-level net) + `tests/contract/test_import_boundaries.py` (AST, the stricter check) |
| CI | `.github/workflows/` - required: `lint-type-test`, `base-install`, `protected-paths`, `rls-proof`, `secrets-scan` |
| Subagents / DMS sequence | `.cursor/AGENTS.md`; sequence F1 -> F7 -> V0 -> V3, gate between milestones `docs/dms/SUPERVISOR_GATE.md`; distill trace `skill_distill/DISTILL.md` |
| Runtime state (gitignored, never commit) | `data/engine/`, `data/bench/`, `data/lakehouse/`, `CortexOS/data/` (per-device key), `packs/data/dms_ops.db` (rebuild: `python -m scripts.seed_ops_db`) |

## Binding rules

1. **Merge flow, in order, on one exact head SHA:** draft PR from a new unused branch ->
   push CI 5/5 SUCCESS (the five required checks above) -> PR Bot exact-head CLEAR ->
   AGREE from a reviewer of a different model family than the writer -> squash-merge
   with `--match-head-commit <head>` -> Check (R-0003: a different run verifies; the
   writer never verifies itself) -> Lead Formal.
2. **Any head move voids CLEAR and AGREE.** A new commit, rebase or force-push means
   CI, CLEAR and AGREE are redone on the new head. The CI `auto-merge` job
   squash-merges any non-draft PR once checks are green and does not look for CLEAR
   or AGREE, so a PR stays draft until both are on its exact head.
3. **Prove is pinned to `279cbd85`** (`279cbd85087464b3eac7c7faec4b0858de3cb5e8`).
   Never tip-deploy prove.
4. **Nothing is PASS without evidence:** the command and its output on the exact SHA.
   STATUS.md, docstrings, a local pytest count and an agent's own claim are not
   evidence and are not a GitHub CI claim.
5. **OpenVault FreeRoute is the only model path** (`CortexOS/integrations/freeroute.py`).
   No provider keys from env or files, no direct provider calls; unarmed fails closed.
   Remaining env/file key paths are debt tracked in #267.
6. **Keep handoff files out of PR diffs** (`CLAUDE_HANDOFF.md`, `CURSOR_HANDOFF.md`,
   output of `scripts/handoff.py --write`).

**PR merge tiers** (`tier:fast` / `tier:full`): `.cursor/rules/merge-perfect.mdc` § Merge tiers is the canonical bar. Until #339 lands, CI `auto-merge` merges any non-draft green PR, so no PR leaves draft before its tier's pre-merge steps are done on its exact head, and a `tier:full` PR stays draft until its manual squash.

## Working alongside other lanes

- Run `git log --oneline -3` before `git commit --amend` or any rebase. Never rewrite a
  commit you did not author.
- Stage explicit paths; never `git add -A`. `.cursor/` needs `git add --sparse` in a
  sparse checkout.
- Off freeze #4/#41-#44; do not mint #42. Agents never start or kill `:8020` (R-0015)
  or `:5000`.

## Verify (each command must be able to fail)

```bash
python -m pytest tests/ -q
ruff check CortexOS packages/cortex_contract scripts tests/packaging tests/contract
mypy
lint-imports                                # NOT `python -m importlinter.cli`
python scripts/check_versions.py
python scripts/export_openapi.py --check    # needs .[full]
python scripts/check_contract_compat.py
```

`python -m importlinter.cli lint` exits 0 without running any contract (seen on
Windows) and looks like a pass. Use the `lint-imports` console script; if it is not on
PATH it is at `%APPDATA%\Python\Python3xx\Scripts\lint-imports.exe`. A gate that
cannot fail is not a gate: confirm a command can report failure before trusting its
exit code.
