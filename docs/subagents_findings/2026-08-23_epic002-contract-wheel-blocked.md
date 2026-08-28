---
keywords: [epic-002, cortex-contract, module-identity, wheel, contract-01, pr4]
main_idea: EPIC-002 packaging is blocked because CONTRACT-01's one-identity fix never reached origin/main; dual import spelling is still live.
models: [composer-2.5]
workflow: none
reuse: golden_rule
status: verified
cite: agent: epic-002-survey
repo: Cortex
date: 2026-08-23
---

# EPIC-002 blocked: dual module identity still on main

## Main idea

- GitHub #5 (CONTRACT-01) is CLOSED (2026-08-21) but its commits are **not** on `origin/main`.
- The closed work lives on `chore/unblock-ci-and-estate-audit` / PR #4, which this lane was told not to attach to.
- `origin/main` still imports `packages.cortex_contract` (26 Python hits) and keeps the try/except shim in `CortexOS/execution/manifest.py`.
- Building or testing a wheel against that tree would certify two `Manifest` classes. Stopped after survey. Did not invent a second identity. Did not publish. Did not open an implementation PR.

## Keywords (search)

`epic-002`, `cortex-contract`, `module-identity`, `canonical_manifest_bytes`, `CONTRACT-01`, `PR #4`

## Questions left open

- When PR #4 (or a slim cherry-pick of `5d27f7c` + `59b0106`) lands on main, the first startable EPIC-002 slice is: in-repo wheel build + subprocess test that `import cortex_contract` from the artifact matches in-repo `canonical_manifest_bytes`.
- Real PyPI publish is a later founder step. No twine / PYPI_TOKEN workflow exists. `release.yml` only attaches the wheel to a GitHub Release on `v*` tags.

## Full answer / evidence

Issue #16 (EPIC-002) still says blocked by #5. #5 closed after independent verify on the **unblock-ci** branch, not after merge to main.

| Check | `origin/main` (2bbb950) | `chore/unblock-ci-and-estate-audit` (cf9c725) |
|---|---|---|
| `from packages.cortex_contract` in `*.py` | 26 | 0 |
| `CortexOS/execution/manifest.py` | try `cortex_contract` except `packages.cortex_contract` | bare `cortex_contract` only |
| name-binding refusal in `packages/cortex_contract/__init__.py` | absent | present (CONTRACT-01) |
| `tests/contract/test_contract_module_identity.py` | absent | present |
| contract version | 1.2.0 | 1.3.0 |
| `python -m build --wheel packages/cortex_contract` | pyproject already maps `cortex_contract = "."` | same |
| PyPI publish script | none (docs/archive/task.md mentions twine historically) | none |

CONTRACT-01 commits (not ancestors of `origin/main`):

- `5d27f7c` fix(contract): one module identity, and a mypy gate that actually runs (Cortex#5)
- `59b0106` fix(contract): refuse the second module identity at name binding (Cortex#5)

Reproduced class of bug (from #5 comments, not re-derived): both spellings load the same `__file__` but `Manifest is Manifest` is False, so `canonical_manifest_bytes` takes the Mapping branch for engine-built models. Bytes agree only while every field is a string.

Existing in-repo packaging (already on main, not sufficient for EPIC-002 acceptance):

- `packages/cortex_contract/pyproject.toml` -- name `cortex-contract`, module `cortex_contract`
- `.github/workflows/release.yml` builds that wheel into GitHub Release artifacts
- `scripts/windows/Build-CortexContractWheel.ps1` local build only; no upload

## Golden rule (if reusable)

> Do not start EPIC-002 wheel/identity tests until `origin/main` imports only `cortex_contract` and `__init__.py` refuses `__name__ != "cortex_contract"`. Dual spelling on main is the CONTRACT-01 bug; a wheel test on that tree would hide it. Do not cherry-pick onto PR #4. Unblock = merge or slim-reapply `5d27f7c` + `59b0106` onto main first.

## Verify

```bash
git merge-base --is-ancestor 59b0106 origin/main   # expect fail (exit 1)
git grep -c "from packages.cortex_contract" origin/main -- "*.py"   # expect 26
git cat-file -e origin/main:tests/contract/test_contract_module_identity.py   # expect missing
```

## Promote?

- [x] `docs/subagents_findings` only
- [ ] `~/.claude/skills|workflows|findings`
- [ ] `~/.cursor/skills|workflows|subagents|findings`
- [ ] `skill_distill/captures` + ingest
