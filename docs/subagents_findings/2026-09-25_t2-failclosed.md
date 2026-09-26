# T2-FAILCLOSED: fail-closed auth by default; no shipped bypass or published keys (#263)

Epic #263 (EPIC-TRUST-02), ticket T2-FAILCLOSED, added by the founder's decision of 2026-09-25 (#263 comments 5825984024 and 5825990095). Base: 110f349. Builds on TRUST-01's engine auth port (`CortexOS/security/auth_port.py`), whose 401 / 403 / 503 semantics are unchanged.

## Expected vs actual (before this change)

- Expected: with `DMS_API_KEYS` unset and no OpenVault authorizer armed, every gated request is refused (no valid key: 401; no registered authorizer: 503). No shipped image turns auth off. No key value is published in the repo. The operator desk works for a properly authenticated operator.
- Actual on 110f349:
  - `packs/dms/security/api_auth.py` fell back to a built-in set of viewer, steward and admin keys whenever `DMS_API_KEYS` was unset, unless `DMS_REFUSE_DEMO_KEYS` was also set. Those key values were published in the repo, so an install that forgot to configure keys accepted publicly known credentials, admin included.
  - `Dockerfile.core` and `Dockerfile.full` set `DMS_AUTH_DISABLED=1` in the image, so those images ran with auth off by default.
  - `SETUP_ONCE.ps1`, `demo/run_demo.ps1`, `scripts/start_cortex_engine.ps1`, `secrets/dms.env.example.yaml`, the demo UI (`demo/dms-ui/lib/api.js`) and its Playwright config carried the same key values as literals.
  - `GET /api/connectors` (the operator desk) sat behind the viewer gate, but a browser navigation cannot send `X-API-Key`, and the page's own fetches sent no key either. A properly authenticated operator therefore got 401 for the page, and the page could not have loaded its data even if it had rendered.

## Root-cause class

Fail-open default: a missing security configuration was treated as "use a built-in credential" (and, in two images, "turn the check off") instead of "refuse". The credential was then published, which turns the default into a known key.

## Fix

- `packs/dms/security/api_auth.py`: the built-in key set and its fallback are deleted. `_keys_source()` returns only what `DMS_API_KEYS` holds, or nothing. With nothing configured, every key is refused with 401 (an `ov_` token is still forwarded to OpenVault for verification, as before, and refused when the vault does not confirm it). `DMS_REFUSE_DEMO_KEYS` is **kept as an accepted, harmless variable with no effect**: `Dockerfile`, `Dockerfile.constructor`, `compose.constructor.yml` and `tests/packaging/test_constructor_hyperlift_dockerfile.py` still carry it, and removing it would widen this ticket into those files for no behaviour change. `auth_bypass_warnings()` reports an active `DMS_AUTH_DISABLED` (and, separately, an install with no keys configured). `DmsRequestAuthorizer` exposes it as `startup_warnings()`.
- `CortexOS/security/auth_port.py`: one additive function, `startup_warnings()` / `log_startup_warnings()`. It asks the registered authorizer for its warnings through an optional method that is not part of the `RequestAuthorizer` protocol, so existing authorizers are unaffected, and reports a missing authorizer as a warning too. Needed because the engine cannot import the pack (C2) and must not learn the pack's env names. The 401 / 403 / 503 behaviour of `require_role` is unchanged.
- `CortexOS/api/app.py`: `create_app()` calls `log_startup_warnings()`, so an app built with `DMS_AUTH_DISABLED` logs `SECURITY WARNING: DMS_AUTH_DISABLED is set: API authentication is OFF ...` at WARNING every time. No new `packs` import (the call goes through the engine port).
- `Dockerfile.core`, `Dockerfile.full`: `DMS_AUTH_DISABLED=1` removed; a comment says to pass `DMS_API_KEYS` or pair OpenVault at run time. `Dockerfile`, `Dockerfile.constructor`, `compose.constructor.yml`: comments that described the core image as setting the bypass are updated (`Dockerfile` and `Dockerfile.constructor` stay byte-identical).
- `SETUP_ONCE.ps1`, `demo/run_demo.ps1`, `scripts/start_cortex_engine.ps1`: the literal key assignment is replaced by `scripts/demo_keys.ps1` (new), which generates random viewer, steward and admin keys once per install with `System.Security.Cryptography.RandomNumberGenerator` (24 bytes each), persists them to `data/local/demo_api_keys.env`, and exports `DMS_API_KEYS` for the engine plus `NEXT_PUBLIC_DMS_VIEWER_KEY`, `NEXT_PUBLIC_DMS_STEWARD_KEY`, `NEXT_PUBLIC_DMS_ADMIN_KEY` for the UI (`next dev` is started from the same process and inherits them). The scripts print the file path, never the values. `.gitignore` gains `data/local/` (confirmed with `git check-ignore -v data/local/demo_api_keys.env` -> `.gitignore:73:data/local/`). `start_cortex_engine.ps1` still honours an existing `DMS_API_KEYS` and prints a warning when `DMS_AUTH_DISABLED` is set.
- `secrets/dms.env.example.yaml`: placeholders (`REPLACE_WITH_RANDOM_*_KEY`) instead of key values.
- `demo/dms-ui/lib/api.js`: keys come from the three `NEXT_PUBLIC_DMS_*_KEY` variables (spelled literally so Next inlines them). A keyed call with no key configured throws `ApiKeyMissingError` naming the variable to set, before any request is sent; `/health` is still called without a key. `AppShell.jsx` shows the same message as a banner. `playwright.config.js` and `e2e/reliability.spec.js` take the key from `NEXT_PUBLIC_DMS_VIEWER_KEY` (or `DMS_E2E_API_KEY`); the config throws a clear error when neither is set.
- `scripts/stream_simulate.py`: `--api-key` defaults to `$DMS_API_KEY` instead of a published literal.
- `CortexOS/api/connector_routes.py`: `GET /api/connectors` is now a static shell on its own router with no auth dependency. It holds no engine data (no roster, no probe result, no workspace or host path; the roster and the computer-control chip are fetched by the page) and is byte-identical for every caller, with `Cache-Control: no-store`. The page asks for the operator key once (`window.prompt`), keeps it in `sessionStorage`, sends it as `X-API-Key` on every fetch, never puts it in a URL, drops it on 401 and shows a clear bar with an "Enter key" button on 401 / 403 / 503. Every data route under `/api/connectors/*` keeps its TRUST-01 gate.
- Tests given explicit keys: `tests/api_key_isolation.py` (new pytest plugin, registered in `pyproject.toml` `addopts` after `tests.runtime_log_isolation`) sets `DMS_API_KEYS` to test-only keys for every test via an autouse `monkeypatch` fixture, so a test or fixture that sets or deletes `DMS_API_KEYS` itself still wins. Seven test files that sent the old literal values now send the plugin's test keys (string swap only). `tests/conftest.py` is untouched.
- `tests/test_api_security/test_app_connector_auth.py` and `test_resp_hardening.py`: `GET /api/connectors` removed from their "every route 401s without a key" lists, and the "every registered route is gated" sweep exempts exactly `("GET", "/api/connectors")` (by method and path, not by prefix). This is the requirement change the ticket makes; the shell's own guarantees moved to `test_fail_closed.py`.

## Invariant

No credential is built into Cortex or published in the repo. An install with no keys configured and no authorizer refuses every gated request (401, or 503 with no authorizer). No shipped Dockerfile sets `DMS_AUTH_DISABLED`; setting it is an explicit local choice that is logged at WARNING. The only ungated path under `/api/connectors` is a static page with no data.

## Tests

`tests/test_api_security/test_fail_closed.py` (17 tests, part of the normal suite, so `lint-type-test` runs them). Assertions are on HTTP status and body, log records and shipped file contents.

Required tests (a) to (d), and the extras:

- (a) `test_no_dockerfile_sets_auth_disabled_to_a_truthy_value`: globs every `Dockerfile*` in the repo (5 found: `Dockerfile`, `.core`, `.full`, `.constructor`, `night_shift/Dockerfile`), drops comments, joins continuations, fails on `DMS_AUTH_DISABLED` set to 1 / true / yes / on in either ENV form. `test_dockerfile_check_catches_both_env_forms` proves the matcher is not vacuous.
- (b) `test_no_keys_configured_refuses_demo_and_builtin_keys`: `DMS_API_KEYS` unset, OpenVault unreachable. `parse_api_keys()` is empty; the three formerly published values and other guesses (`admin`, `demo`, `ov_demo`) get 401 with the refusal text on an engine-port-gated route and a pack-gated route; then with the authorizer cleared, the port-gated route answers 503 with the fixed body, with or without a key. `test_refuse_demo_keys_variable_is_accepted_and_changes_nothing` covers the kept variable.
- (c) `test_published_demo_keys_are_not_in_setup_or_secret_templates`: none of the three values in `SETUP_ONCE.ps1` or `secrets/*.example*`. Static checks for the demo, since PowerShell and Next are not run here: `test_local_demo_generates_random_per_install_keys` (the helper uses `RandomNumberGenerator`, writes `data\local\demo_api_keys.env`, exports all four variables; every launcher dot-sources it and has no literal `DMS_API_KEYS` assignment or published value), `test_local_demo_key_file_is_gitignored` (`git check-ignore` exit 0), `test_demo_ui_reads_keys_from_env_and_says_so_when_unset`.
- (d) `test_valid_key_with_sufficient_role_sees_unchanged_response`: with keys configured, an operator loads the desk shell with no header, then every desk data route and a pack-gated route answer each of viewer / steward / admin with the same JSON as the ungated route; a steward post lands in the inbox; below-role is still 403; published values still 401.
- Desk: `test_desk_shell_loads_without_a_key_and_carries_no_data` (200 HTML without a key; no roster text, no probe field, no tmp, repo or home path; `X-API-Key` and `sessionStorage` used, no `localStorage`, no key in a URL; identical bytes with any key), `test_desk_data_calls_still_refuse_without_a_valid_key` (every desk data route 401 with no key or a published value), and `test_desk_in_a_browser_asks_once_and_sends_the_key_on_every_call` (real Chromium via Playwright against uvicorn: one prompt, key in `sessionStorage`, every `/api/connectors/*` request carries it, none has it in the URL, agents render; a refused key is dropped and the 401 bar is shown with no data). It follows the browser-gate pattern: skips locally without a browser, fails under `CORTEX_BROWSER_GATE=required`.
- Bypass warning: `test_auth_bypass_logs_a_warning_when_the_app_is_built` and `test_no_bypass_warning_on_a_normal_keyed_install`.
- Plugin: three tests prove the default test keys are present and that a test deleting or a fixture setting `DMS_API_KEYS` wins.

Fail on base (the 19 changed non-test source files swapped for `git show 110f349:<path>`, `scripts/demo_keys.ps1` removed, the test file and the plugin kept, then everything restored): 12 of 17 fail, including every required one:
- (a) fails: `Dockerfile.core` and `Dockerfile.full` set `DMS_AUTH_DISABLED=1`.
- (b) fails: `parse_api_keys()` returns the three built-in keys; the refuse-variable test gets 200 for a published value.
- (c) fails: `SETUP_ONCE.ps1` carries the values; the helper file is missing; `data/local/` is not ignored; `api.js` has no env read.
- (d) fails at the first step: the desk shell answers 401 to a browser load. Its key-path assertions (valid key, sufficient role, unchanged JSON) pass on base by design, since they guard against a false positive.
- Desk shell, desk data (a published value got 200 on base), browser and bypass-warning tests fail.
- The 5 that pass on base are guards that hold on both sides by construction: the Dockerfile matcher self-test, the no-warning-on-a-keyed-install test and the three plugin tests.

## Verified vs assumed

- VERIFIED: the fail-on-base run above; `tests/test_api_security/test_fail_closed.py` 17 passed; the full suite `python -m pytest tests/ -q -p no:cacheprovider` exit 0 with 2988 passed, 12 skipped, 4 xfailed (base 110f349: 2971 passed, 12 skipped, 4 xfailed; +17 is the new file), and the same counts, exit 0, with `CORTEX_BROWSER_GATE=required`; `pytest tests/contract` 95 passed; `tests/test_crew/test_cot_climb.py::test_branch_does_not_dual_write_freeroute_layer` passed; `lint-imports` 3 kept, 0 broken; `scripts/check_versions.py` OK; `scripts/secrets_scan.py` clean; ruff clean on the changed engine, pack, script and new test files.
- VERIFIED with node 22 (not Next): `lib/api.js` copied to an ES module and run with a stubbed `fetch`. With the variables unset, `fetchTables()` throws `ApiKeyMissingError` ("No Cortex API key configured for role ANALYST: set NEXT_PUBLIC_DMS_VIEWER_KEY ...") and sends nothing, and `checkHealth()` still calls `/health` without a key. With them set, each role's call carries its key. The desk script and both Playwright files pass `node --check`.
- NOT EXECUTED: PowerShell is not installed (`pwsh` absent), so `SETUP_ONCE.ps1`, `demo/run_demo.ps1`, `scripts/start_cortex_engine.ps1` and `scripts/demo_keys.ps1` were not run; they are covered by the static tests only. `next dev` / `next build` and the demo UI's Playwright e2e were not run (no `node_modules` in `demo/dms-ui`). `scripts/export_openapi.py --check` was not run (`.[full]` extras not installed); `/api/connectors` is not in `contract/openapi-1.2.0.json` and no contract model changed.
- Pre-existing, not introduced here: ruff reports I001 / B011 in `tests/dms/test_f2_chat.py`, `test_s0_streams.py`, `test_s1_agents.py` on lines this change did not touch (only key literals changed in those files); mypy's two findings in touched files are on unchanged lines (`app.py` optional-routes loop, `api_auth._caller_from_openvault`).
- ASSUMED: no deployment relies on the core or full image running with auth off, or on the built-in keys. Any that does now gets 401 until it configures `DMS_API_KEYS` or OpenVault. That is the intended outcome of the decision.

## Coordination

- DMS Studio must call Cortex with a real key issued through OpenVault before #235 merges; after this lands, a Studio call with no key or a formerly published value gets 401. Platform is relaying this to DMS Pipeline. Per the founder's merge rule, #235 merges only after this commit is on its tip with CI green.
- Anyone running `Dockerfile.core` or `Dockerfile.full` must now pass `DMS_API_KEYS` or `OPENVAULT_BASE_URL` at run time.
- Local demo users: the first run of `SETUP_ONCE.ps1` or `demo/run_demo.ps1` creates `data/local/demo_api_keys.env`; delete it to rotate. An already-running `next dev` must be restarted to pick the keys up.
- Out of scope and untouched: `/v1/contract/*` auth (separate ticket), ledger append actor, Crew and `/dms/query` credential spending, keys outside OpenVault, and the OpenVault verify endpoint gap.

## Coordinator fix after verify round 1 (2026-09-25)

The verifier passed 0310f14, with one nonblocking note that belongs to the same failure class as this ticket: `secrets/dms.env.example.yaml` ships `admin:REPLACE_WITH_RANDOM_ADMIN_KEY`, and `parse_api_keys` accepted it as a working admin key. A deploy that copies the template unchanged would get a publicly known admin key. Pasting the old published demo values into `DMS_API_KEYS` explicitly would do the same.

Fix: `parse_api_keys` skips any key that starts with `replace_with` (case-insensitive) or that equals one of the three formerly published demo values, and logs a warning naming the role. Real keys configured next to a placeholder still work.

Tests: `test_template_placeholders_and_published_values_never_authenticate` (3 cases), `test_example_template_keys_parse_to_nothing` and `test_real_keys_next_to_a_placeholder_still_work`. All 5 fail on 0310f14 and pass here. The full suite passed with exit 0.
