# Dependabot bumps triage: 7 open PRs, none merge-ready as they stand (2026-10-30)

Triage of the open dependabot PRs against main `aae3cbc99b22610feb3ee400f03741a7dda5d2f9`
(CI green on push run 40995556218). Nothing was merged, closed or pushed to a
dependabot branch. Two `@dependabot rebase` comments were posted (#441, #442).

## Decision table

| PR | Version change | CI on head | Breaking-change notes | Decision | Evidence |
|---|---|---|---|---|---|
| #442 | @babel/runtime 8.0.7 -> 8.0.8 (demo/dms-ui, dev) | 4/5 on `b6e682d`: `lint-type-test` red | None. 8.0.8 changelog: one bug fix touching runtime helpers (#18186, `_arrayWithHoles` with array-backed Proxies). | **Rebase requested, then merge-ready** if CI 5/5 on the new head | Red is the 6 `tests/dms/test_rag01_document_answer.py` failures (`'reconfirm' == 'abstain'`) inherited from base `0b9f169` (main push run 40982793385 red); fixed on main by #443. Diff is `package.json` + `package-lock.json` only. 2 commits behind, merges clean. `@dependabot rebase` posted. |
| #441 | @babel/core 8.0.7 -> 8.0.9 (demo/dms-ui, dev) | 4/5 on `fd00c97`: `lint-type-test` red | None listed. Babel's CHANGELOG has no 8.0.9 section (8.0.8, then 8.0.10); npm shows 8.0.9 published 2026-10-23, not deprecated. npm `latest` is now 8.0.10. | **Rebase requested, then merge-ready** if CI 5/5 on the new head | Same inherited RAG-01 failure as #442 (job 123845279722). Lock diff moves @babel/core, helpers, parser, types to 8.0.8/8.0.9 only. `@dependabot rebase` posted. |
| #396 | python-dotenv 1.2.2 -> 1.2.4 | 4/5 on `ce576e9`: `lint-type-test` red | None marked breaking. Behaviour changes: 1.2.3 strips a UTF-8 BOM, `set_key` escapes backslashes; 1.2.4 parses `KEY= # comment` as empty, `dotenv run --no-override` expansion precedence. No first-party import of `dotenv` (transitive via litellm, pydantic-settings, uvicorn). | **Needs fix**: replace with a PR made by the repo's lock tooling | `check_supply_chain.py` on the head: `FAIL image-*.lock.txt: python-dotenv==1.2.4 disagrees with uv.lock python-dotenv==1.2.2` (x3). Dependabot edited the 3 image locks and not `uv.lock`. Fix proven in a scratch worktree of main: `uv lock --upgrade-package python-dotenv` then `python scripts/lock_images.py --align-uv-lock` changes 4 files (uv.lock + 3 locks, 13/13 lines) and `check_supply_chain.py` prints `supply chain OK`. |
| #394 | nvidia-cufile 1.15.1.6 -> 1.19.1.55 | 4/5 on `1a634dd`: `lint-type-test` red | Unresolvable. torch 2.13.0 requires `cuda-toolkit[cufile]==13.0.3`, which pins `nvidia-cufile==1.15.1.6.*`. | **Close**: moves only when torch moves | `uv pip compile` of `torch==2.13.0` + `nvidia-cufile==1.19.1.55` (py3.11, x86_64 Linux): `No solution found`. Supply-chain check on head: `nvidia-cufile==1.19.1.55 disagrees with uv.lock nvidia-cufile==1.15.1.6`. Present only in `image-full.lock.txt` (torch via sentence-transformers). |
| #393 | multidict 6.9.1 -> 7.1.0 (major, draft) | 4/5 on `8163389`: `lint-type-test` red | 7.0.0: `istr` is final. 7.1.0: `keys()` and iteration yield each key once; `CIMultiDict` no longer calls a `str` subclass's own `lower()`; `multidict.upstr` deprecated. aiohttp 3.14.3 (locked) requires `multidict<7.0`; aiohttp 3.14.5 allows `<8.0`. No first-party import of multidict, aiohttp or yarl. | **Close**, redo as a coordinated bump | Unresolvable as proposed: `aiohttp==3.14.3` + `multidict==7.1.0` gives `No solution found`; supply-chain check on head fails x3. `uv lock --upgrade-package multidict --upgrade-package aiohttp --upgrade-package yarl` on main resolves to aiohttp 3.14.5, multidict 7.2.1, yarl 1.25.1, so 7.1.0 is already stale. Local run on main `aae3cbc` in a fresh py3.11 venv, `.[dev]` plus `multidict==7.1.0 aiohttp==3.14.5 yarl==1.25.1` (`uv pip check`: all compatible): `pytest tests/ -q` gave 4277 passed, 15 skipped, 4 xfailed. Not covered: the 15 skips were not itemised, and `.[full]` (CI's env) was not installed. |
| #390 | nvidia-cublas 13.1.1.3 -> 13.8.1.7 | 4/5 on `dce7656`: `lint-type-test` red | Unresolvable. `cuda-toolkit[cublas]==13.0.3.0` pins `nvidia-cublas==13.1.1.3.*` (also pulled by its `cusolver` extra). | **Close**: moves only when torch moves | `uv pip compile` of `torch==2.13.0` + `nvidia-cublas==13.8.1.7`: `No solution found`. CI log (job 116770105598): `FAIL image-full.lock.txt: nvidia-cublas==13.8.1.7 disagrees with uv.lock nvidia-cublas==13.1.1.3`. |
| #389 | nvidia-cusolver 12.0.4.66 -> 12.3.4.7 | 4/5 on `7f37091`: `lint-type-test` red | Unresolvable. `cuda-toolkit[cusolver]==13.0.3.0` pins `nvidia-cusolver==12.0.4.66.*`. | **Close**: moves only when torch moves | `uv pip compile` of `torch==2.13.0` + `nvidia-cusolver==12.3.4.7`: `No solution found`. Supply-chain check on head fails on the same pin. |

CI "4/5" means `base-install`, `protected-paths`, `rls-proof`, `secrets-scan` green and
`lint-type-test` red; `auto-merge` skipped. All 7 heads merge into main without conflict
(`git merge-tree`); the pip heads are 30-44 commits behind.

## Expected vs actual
- **Expected:** a dependabot pip PR moves one pin and stays green, because the image locks
  are generated from `uv.lock` by `scripts/lock_images.py` and must agree with it
  (`scripts/check_supply_chain.py` rule 5).
- **Actual:** all 5 pip PRs rewrite `requirements/image-*.lock.txt` directly and leave
  `uv.lock` alone, so rule 5 fails on every one. Four of the five also propose versions
  no resolver can install with the rest of the lock (exact `cuda-toolkit` pins from torch;
  aiohttp's `multidict<7.0` cap). The CI gate caught all of them.
- The CUDA libraries are not "unused by CPU images" as assumed: `image-full.lock.txt`
  installs torch 2.13.0 from PyPI through `sentence-transformers`, and that wheel pulls
  `cuda-toolkit` and the `nvidia-*` libs on Linux x86_64. They close because they are
  pinned by torch, not because they are absent.

## Repro
1. `git fetch origin dependabot/pip/<branch>` and `git worktree add --detach <dir> FETCH_HEAD`.
2. `cd <dir> && python scripts/check_supply_chain.py` (the branch's own copy; the script
   resolves `ROOT` from its own path, so running main's copy against a worktree checks
   main and prints OK).
3. Resolver proof: `uv pip compile --python-version 3.11 --python-platform x86_64-unknown-linux-gnu`
   on a file holding `torch==2.13.0` and the bumped `nvidia-*` pin; or `aiohttp==3.14.3`
   and `multidict==7.1.0`.

## Root-cause class
A bot edits a generated artifact by hand. Dependabot's `pip` ecosystem treats
`requirements/image-*.lock.txt` as plain requirement files and bumps lines in them, while
the source of truth is `uv.lock` plus `scripts/lock_images.py`. Every pip PR it opens
here will fail rule 5 until the repo stops pointing that ecosystem at the image locks.

## Invariants
- "Never hand-author generated artifacts, regenerate them" (CLAUDE.md global rules;
  `lock_images.py` docstring: "never edited by hand").
- "Prove a gate can fail before trusting it green": the supply-chain check did fail on
  each head; an earlier run of main's copy against the worktrees printed OK for all five
  and was discarded as invalid (see Repro step 2).

## Next steps (not done here)
1. After dependabot rebases #441 and #442 and CI is 5/5 on the new heads, they follow the
   normal merge flow (CLEAR + AGREE on the exact head).
2. Replace #396 with a PR from a new branch built by
   `uv lock --upgrade-package python-dotenv && python scripts/lock_images.py --align-uv-lock`.
3. Close #394, #390, #389. Their versions move with the next torch bump.
4. Close #393; if multidict 7 is wanted, one PR moving aiohttp, multidict and yarl
   together through the same two commands.
5. Stop the recurrence (open design question, not decided here): switching
   `.github/dependabot.yml` to the `uv` ecosystem would move `uv.lock` but still leave the
   image locks stale, so rule 5 would fail the other way. Dependabot cannot run
   `lock_images.py`; the choices are a workflow that regenerates the image locks on
   dependabot branches, or treating dependabot pip PRs as notices that a human
   regenerates. Either needs a check that a bot PR then passes rule 5.
