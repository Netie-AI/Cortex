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

- `tests/conftest.py`
  - `runtime_logs_isolated` (autouse, function scope): sets
    `CORTEX_DECISION_LOG_PATH` and `CORTEX_KEV_SHADOW_PATH` to
    `<tmp_path>/runtime_logs/<default filename>` for every test. Autouse fixtures
    run before a test's own fixtures and body, so a test that sets either env
    itself through `monkeypatch.setenv` (all of `tests/test_decision/`) keeps its
    own path; the two tests that `delenv` the override to assert the default path
    still see `data_path("engine", ...)` because they delete after the fixture ran.
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
    baseline, a `snapshot()` callable, the real paths and the env map. The
    conftest module is deliberately not imported by name (see `_freeroute_fake`
    for why `import tests...` is unsafe in this repo).
- `tests/test_meta/test_runtime_logs_untouched.py`: 7 tests.
  - env names in conftest equal `decision_log.PATH_ENV` / `shadow.PATH_ENV` and the
    protected paths equal `data_path("engine", <default filename>)`;
  - both real files are unchanged against the session-start baseline at the point
    this test runs;
  - `decision_log.log_path()` and `shadow.shadow_path()` resolve under the test's
    `tmp_path`;
  - a real `run_dag` with an `llm_judged` node writes exactly one decision row at
    the isolated path (run id, node id, status read back from the file) and both
    real files are unchanged against the session-start baseline;
  - a real shadow evaluation (`JudgmentModel.from_env()` with `CORTEX_KEV_SHADOW=1`
    and a respx kev) writes one shadow row at the isolated path and both real
    files are unchanged;
  - no-false-positive: a test that sets both env vars itself gets its rows at its
    own path and nothing at the fixture's path;
  - `CORTEX_DECISION_LOG=0` is still honoured.

## Repro

Base (275fe13), in a checkout with no `data/engine/`:

```
python -m pytest tests/ -q -p no:cacheprovider; echo rc=$?
wc -l data/engine/tier_decisions.jsonl     # 46 on base
```

Change:

```
rm -rf data/engine
CORTEX_RUNTIME_LOG_GUARD=1 python -m pytest tests/ -q -p no:cacheprovider; echo rc=$?
ls data/engine/tier_decisions.jsonl data/engine/tier_shadow.jsonl   # both absent
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

## Conflict the caller has to resolve

`tests/test_crew/test_cot_climb.py::test_branch_does_not_dual_write_freeroute_layer`
(landed with #234) lists `tests/conftest.py` among the #215 FreeRoute files a
branch must not touch, and asserts the name diff against origin/main has no
overlap. Issue #254 names `tests/conftest.py` as the write target, and the root
conftest is the only place an autouse fixture can cover the whole suite (a
`pytest_plugins` entry or `addopts -p` would need `tests/conftest.py` or
`pyproject.toml`, neither of which escapes the ban). So with this change on the
branch that guard fails whenever origin/main is reachable, and it will fail in CI.
The guard is not a write target of this ticket and was left alone. Resolution
options, for the owner: drop `tests/conftest.py` from the banned set now that
d8ec70d (the #215 conftest change) is on origin/main, or move the ban to a
content assertion on the FreeRoute fixtures (`freeroute_hermetic`,
`fake_openvault`, `armed_openvault`), which this change does not touch.

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
- `CortexOS/**` untouched; `CortexOS/**` still never imports `packs.*`.
- Do-not-touch list respected: no change under `CortexOS/**`, `tests/contract/**`,
  `tests/invariants/**`, `.importlinter`, `contract/**`.

## Verified vs assumed

VERIFIED:
- Base repro: full suite on 275fe13 wrote 46 rows to
  `data/engine/tier_decisions.jsonl` (file absent before the run). Base result:
  1 failed, 2360 passed, 13 skipped, 4 xfailed, exit 1; the one failure was `tests/test_crew/test_cot_climb.py::test_branch_does_not_dual_write_freeroute_layer`, which diffs the working tree against origin/main and fired because this lane's conftest edit was already on disk mid-run; see Conflict below.
- New tests: 7 pass on the change. With `tests/conftest.py` swapped for
  `git show 275fe13:tests/conftest.py` (restored afterwards), 6 of 7 fail:
  `test_engine_resolves_both_logs_to_a_per_test_tmp_path` (log_path resolves to data/engine), `test_routed_dag_writes_tmp_log_and_leaves_runtime_files_untouched` (the real file grew from 46 to 47 rows and the path the engine wrote held 47 rows instead of 1), `test_shadow_evaluation_writes_tmp_log_and_leaves_runtime_files_untouched` (shadow_path resolves to data/engine), `test_disabled_log_still_writes_nothing` (the real file exists), and `test_fixture_env_names_match_the_engine_modules` plus `test_runtime_files_unchanged_since_session_start` error on the missing `runtime_log_guard` fixture. The one that passes on base is the no-false-positive test
  (a test that sets its own path is correct on base too).
- Full suite on the change with `CORTEX_RUNTIME_LOG_GUARD=1` and `data/engine/`
  removed first: 1 failed, 2367 passed, 13 skipped, 4 xfailed, exit 1 (checked directly). The 2367 is the 2360 of base plus the 7 new tests; the one failure is the origin/main dual-write guard described under Conflict, and no H2-TEST-ISOLATION section was printed, so the guard hook saw both files untouched; afterwards neither runtime file exists.
- ruff on the two changed files: clean.
- mypy: 41 errors in 26 files, identical to the stated baseline, none in the changed files.
- lint-imports: 3 kept, 0 broken, exit 0.
- tests/contract: 95 passed, exit 0.

ASSUMED:
- A test that sets the env at module import time (plain `os.environ[...] =`)
  rather than through `monkeypatch` would be overridden by the autouse fixture;
  no such test exists in the tree today (`grep -rn "CORTEX_DECISION_LOG_PATH\|CORTEX_KEV_SHADOW_PATH" tests/`
  shows only `monkeypatch.setenv` / `delenv` uses).
- Other runtime files under `data/engine/` (sqlite scoreboards, step journal and
  so on) are still written by the suite; this ticket covers only the two JSONL
  logs KEV-CALIB reads.

## Not done

- CI does not yet export `CORTEX_RUNTIME_LOG_GUARD=1` (ci.yml is outside the write
  targets).
- The 138 rows already on the laptop are not cleaned by this change; they are
  gitignored runtime state and should be deleted by hand before any calibration run.
