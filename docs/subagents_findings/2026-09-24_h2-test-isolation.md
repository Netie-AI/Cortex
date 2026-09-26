# H2-TEST-ISOLATION: tests never write the runtime decision or shadow logs (#254)

Epic #249 EPIC-HARDEN-2. Base 275fe13. Worktree lane wf_41d6fc93-b7a-5.

## Expected vs actual

Expected (issue #254): after `python -m pytest tests/ -q` the runtime files
`data/engine/tier_decisions.jsonl` and `data/engine/tier_shadow.jsonl` are
byte-identical to what they were before the run (or still absent). KEV-CALIB
(`scripts/kev_calibration_report.py`) reads the decision log at its default path
as real outcomes, so a synthetic row from a test is a poisoned calibration sample.

Actual on 275fe13 (reproduced here, see Repro): with `data/engine/` absent before
the run, the full suite leaves `data/engine/tier_decisions.jsonl` with
46 synthetic rows (run ids from `tests/test_execution/test_dag_pii_redaction.py`,
`test_parallel_cost_ceiling.py`, `test_judgment_rules_floor.py` and other routed-DAG
tests that never set `CORTEX_DECISION_LOG_PATH`). The verifier's count of 138 on the
laptop is the same defect accumulated over more runs; the file is append-only and
nothing truncates it. `tier_shadow.jsonl` is not written on base because every
shadow test already sets `CORTEX_KEV_SHADOW_PATH`, but nothing enforced that.

## What was built

Revised in the follow-up commit (escalation fix): the first version put the
fixture in `tests/conftest.py`, which the #215 dual-write guard freezes (see
"Conflict (resolved)"). `tests/conftest.py` is now byte-identical to base 275fe13
and to origin/main (`git diff origin/main -- tests/conftest.py` is empty). The
same code lives in a pytest plugin instead.

- `tests/runtime_log_isolation.py` (new pytest plugin), registered for the whole
  suite by `pyproject.toml` `[tool.pytest.ini_options] addopts = "-p tests.runtime_log_isolation"`:
  - `runtime_logs_isolated` (autouse, function scope): sets
    `CORTEX_DECISION_LOG_PATH` and `CORTEX_KEV_SHADOW_PATH` to
    `<tmp_path>/runtime_logs/<default filename>` for every test. Autouse fixtures
    run before a test's non-autouse fixtures and body, so a test that sets either
    env itself through `monkeypatch.setenv` (all of `tests/test_decision/`) keeps
    its own path; the two tests that `delenv` the override to assert the default
    path still see `data_path("engine", ...)` because they delete after the
    fixture ran.
  - `pytest_sessionstart` snapshots `(size, mtime_ns)` or `None` for both real
    files, resolved from the repo root the way `CortexOS.paths.data_path` does,
    ignoring any env override so the snapshot is of the protected files even when
    a shell exports one.
  - `pytest_sessionfinish` re-snapshots and prints a red `H2-TEST-ISOLATION`
    section naming any file that changed. With `CORTEX_RUNTIME_LOG_GUARD=1` it also
    forces a non-zero exit. The failure is opt-in because on the founder's laptop a
    live engine can legitimately append real decisions while the suite runs, and a
    guard that fails a green run over unrelated production traffic is itself a
    defect; CI has no live engine and should export the variable.
  - `runtime_log_guard` (session fixture): hands the meta test the session-start
    baseline, a `snapshot()` callable, the real paths and the env map.
- `tests/test_meta/test_runtime_logs_untouched.py`: 9 tests.
  - the plugin is registered and comes from `addopts` (so removing the
    pyproject line fails loudly, not silently);
  - env names in the plugin equal `decision_log.PATH_ENV` / `shadow.PATH_ENV` and
    the protected paths equal `data_path("engine", <default filename>)`;
  - both real files are unchanged against the session-start baseline at the point
    this test runs;
  - `decision_log.log_path()` and `shadow.shadow_path()` resolve under the test's
    `tmp_path`;
  - a real `run_dag` with an `llm_judged` node writes exactly one decision row at
    the isolated path (run id, node id, status read back from the file) and both
    real files are unchanged;
  - a real shadow evaluation (`JudgmentModel.from_env()` with `CORTEX_KEV_SHADOW=1`
    and a respx kev) writes one shadow row at the isolated path and both real
    files are unchanged;
  - no-false-positive, twice: a test that sets both env vars in its body, and a
    test whose own (non-autouse) fixture sets them, gets its rows at its own path
    and nothing at the plugin's path;
  - `CORTEX_DECISION_LOG=0` is still honoured.

## Repro

Base (275fe13), in a checkout with no `data/engine/`:

```
python -m pytest tests/ -q -p no:cacheprovider; echo rc=$?
wc -l data/engine/tier_decisions.jsonl     # 46 on base
```

Change:

```
rm -f data/engine/tier_decisions.jsonl data/engine/tier_shadow.jsonl
CORTEX_RUNTIME_LOG_GUARD=1 PYTHONPATH=$PWD python -m pytest tests/ -q -p no:cacheprovider; echo rc=$?
ls data/engine/tier_decisions.jsonl data/engine/tier_shadow.jsonl   # both absent
```

Plugin disabled (the control can fail):

```
PYTHONPATH=$PWD python -m pytest tests/test_meta -q -p no:cacheprovider -o addopts=""
```

CI-friendly check (no shell diffing needed): export `CORTEX_RUNTIME_LOG_GUARD=1`
in the pytest step. The session then exits non-zero and prints which file moved,
even if every test passed. `.github/workflows/ci.yml` is not a write target of this
ticket, so the variable is documented here rather than wired.

Guard can fail (checked, not assumed): a throwaway test that appended one byte to
`data/engine/tier_decisions.jsonl` made `python -m pytest tests/test_meta -q`
print the section and exit 1 under `CORTEX_RUNTIME_LOG_GUARD=1`, and
exit 0 with the section printed without the variable. The throwaway test was
deleted afterwards.

## Conflict (resolved)

`tests/test_crew/test_cot_climb.py::test_branch_does_not_dual_write_freeroute_layer`
(another lane's guard, #234/#215) lists `tests/conftest.py` among the FreeRoute
files a branch must not touch, and asserts the name diff against origin/main has
no overlap. The first version of this change edited `tests/conftest.py` and so
failed that guard. Resolution: the guard was left alone, `tests/conftest.py` was
reverted to base, and the fixture moved to a plugin loaded through
`pyproject.toml` `addopts`. `pyproject.toml` is not in the banned set. Earlier
note that `addopts -p` "would not escape the ban" was wrong: only
`tests/conftest.py` is banned, not `pyproject.toml`.

Design notes:
- `-p tests.runtime_log_isolation` imports `tests` as a namespace package (no
  `tests/__init__.py`). It resolved to this checkout both with `PYTHONPATH=$PWD`
  and from `/tmp` with `PYTHONPATH` empty. ASSUMED, not tested: if a different
  checkout that also has `tests/runtime_log_isolation.py` comes earlier on
  `sys.path` (an editable install of another worktree), that copy loads instead.
  That is the same hazard `_freeroute_fake` describes, and it is harmless while
  the copies are identical.
- `-p no:tests.runtime_log_isolation` is NOT a clean way to disable the plugin:
  pytest applies `addopts` first, so the module is imported and its fixtures are
  parsed before the block unregisters it. The autouse fixture stays active and
  only the session hooks and `has_plugin` go away (observed: 1 failed, 2 errors,
  6 passed, no runtime files written). The real off switch is `-o addopts=""` or
  deleting the pyproject line.

## Root-cause class

Test isolation by convention: the engine's default output path is real runtime
state, and isolation depended on each test author remembering the env override.
The KEV-LOG tests did; the routed-DAG tests written for other tickets (PII
redaction, cost ceiling, rules floor) exercised `invoke_routed_completion` for
other reasons and had no reason to know a log existed.

## Invariant

- Every test runs with `CORTEX_DECISION_LOG_PATH` and `CORTEX_KEV_SHADOW_PATH`
  under its own `tmp_path` unless it sets them itself.
- A full suite run leaves `data/engine/tier_decisions.jsonl` and
  `data/engine/tier_shadow.jsonl` byte-identical (size and mtime) or absent.
- `tests/conftest.py` identical to origin/main (#215 dual-write guard).
- `CortexOS/**` untouched; `CortexOS/**` still never imports `packs.*`.
- Do-not-touch list respected: no change under `CortexOS/**`, `tests/contract/**`,
  `tests/invariants/**`, `.importlinter`, `contract/**`.

## Verified vs assumed

VERIFIED (first version, conftest-based, 261ccb8):
- Base repro: full suite on 275fe13 wrote 46 rows to
  `data/engine/tier_decisions.jsonl` (file absent before the run). Base result:
  1 failed, 2360 passed, 13 skipped, 4 xfailed, exit 1 (the dual-write guard,
  because this lane's conftest edit was already on disk mid-run).
- 6 of the first 7 meta tests fail on 275fe13 (details in git history of this
  doc); the no-false-positive body test passes on base, as it should.

VERIFIED (this version, plugin-based):
- `git diff origin/main -- tests/conftest.py` and `git diff 275fe13 -- tests/conftest.py`
  are both empty.
- `test_branch_does_not_dual_write_freeroute_layer` passes (was failing on 261ccb8).
- Full suite with `data/engine/tier_decisions.jsonl` and `tier_shadow.jsonl`
  deleted first and `CORTEX_RUNTIME_LOG_GUARD=1`: 2370 passed, 13 skipped,
  4 xfailed, rc=0 (checked directly). No H2-TEST-ISOLATION section printed; both
  files still absent afterwards. 2370 = base 2361 (2360 + the guard now passing)
  + 9 meta tests.
- Plugin disabled with `-o addopts=""`: `tests/test_meta` gives 5 failed,
  2 errors, 2 passed, and both runtime files were created (1 row each), which
  proves the plugin is what keeps them absent. The files were deleted afterwards.
- New tests against 261ccb8 (the previous commit, conftest fixture): the new meta
  file copied into a scratch worktree at 261ccb8 gives 1 failed, 8 passed. The
  failing one is `test_isolation_plugin_is_registered_for_the_whole_suite`. The
  other new test, `test_path_set_by_a_test_fixture_is_not_overridden`, passes on
  261ccb8 and would pass on 275fe13 too. It is a no-false-positive guard: it
  checks correct behaviour that the previous commit already had, so it cannot
  fail there.
- ruff check on the changed Python files: clean. (`ruff format --check` would
  wrap two lines; CI does not run ruff format, and the lines were left as they
  were written.)
- lint-imports: 3 kept, 0 broken, rc=0. tests/contract: 95 passed, rc=0.

ASSUMED:
- A test that sets the env at module import time (plain `os.environ[...] =`)
  rather than through `monkeypatch` would be overridden by the autouse fixture;
  no such test exists in the tree today.
- Other runtime files under `data/engine/` (sqlite scoreboards, step journal and
  so on) are still written by the suite; this ticket covers only the two JSONL
  logs KEV-CALIB reads.

## Not done

- CI does not yet export `CORTEX_RUNTIME_LOG_GUARD=1` (ci.yml is outside the write
  targets).
- The 138 rows already on the laptop are not cleaned by this change; they are
  gitignored runtime state and should be deleted by hand before any calibration run.
