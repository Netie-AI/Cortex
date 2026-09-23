```yaml
keywords: [ci, playwright, browser-gate, skip, CORTEX_BROWSER_GATE, grant-03, race, boot, spaceId]
main_idea: "CI was green while the GRANT-03 dialog browser tests were skipped. CI now installs Chromium and a skip is a failure there. A boot race in the dialog harness is fixed."
workflow: coordinator
status: raw
repo: Cortex
date: 2026-09-23
```

# BROWSER-GATE-CI: the dialog's browser tests never ran in CI

PREFLIGHT: PARTIAL (KB is Windows-only; used INDEX.md and the GRANT-03 finding)

## Expected vs actual
- Expected: PR #235 CI green means the GRANT-03 security tests ran (hostile window title renders as text, Cancel never posts allow, space id keys the grant).
- Actual: CI `lint-type-test` on 21f5d84 reported `2190 passed, 16 skipped`. Locally the same commit was `2195 passed, 12 skipped`. The 5 extra skips (the `sssss` block) are the browser tests: CI does not install python `playwright`, so the `browser` fixture skipped. Green CI certified a gate that never ran.

## Repro
1. `pip uninstall playwright` (or hide it with a `sitecustomize` that sets `sys.modules["playwright.sync_api"] = None`).
2. `python -m pytest tests/test_crew/test_session_grant_dialog.py -q` gives `2 passed, 5 skipped`.

## Fix
- `tests/test_crew/test_session_grant_dialog.py`: `browser_gate_unavailable()` fails instead of skipping when `CORTEX_BROWSER_GATE=required`; local runs still skip with the loud reason.
- `.github/workflows/ci.yml` `lint-type-test`: installs `playwright==1.56.0` plus `chromium --with-deps`, and sets `CORTEX_BROWSER_GATE=required` on the Pytest step.
- Proved the gate can fail: playwright hidden + required = 5 errors; hidden + unset = 5 skipped; present + required = 7 passed.

## Second defect found while proving the gate
`test_browser_session_id_defaults_to_the_current_space_id` failed 2 of 6 runs under parallel load. Root cause: the page's async `boot()` calls `selectSpace(state.spaces[0].id)`, but the fixture only waited for `crewAskAccess` to exist (defined synchronously). Under load boot finished after the test set `state.spaceId` and overwrote it. The fixture now also waits for `state.spaceId !== null`. After the fix: 12 of 12 under the same load. Harness race, not a product bug.

## Root-cause class
Skip-as-pass: an optional dependency missing in CI turns a security test into a silent skip while the job stays green.

## Invariants
- A skipped test is a failing test. Fail loud with cause.
- Prove a gate can fail before trusting it green.
- "Flake" is not a root cause (the race was reproduced and fixed, not retried).

## Verified vs assumed
- Verified locally: gate fails/skips/passes as above; full suite with `CORTEX_BROWSER_GATE=required`: 2195 passed / 12 skipped / 4 xfailed.
- Assumed until CI runs: `playwright install --with-deps chromium` succeeds on the GitHub ubuntu runner, and the 4 other playwright-dependent tests (reliability/discovery) pass there as they do locally. The next CI run on #235 is the proof.
