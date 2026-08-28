# CI/CD gate — org billing FAIL, code vectors PASS

| Field | Value |
|-------|-------|
| Date | 2026-08-28 |
| Keywords | cicd, billing, actions, github, email, pr-85, start_spawn, python |
| Main idea | Every Cortex CI email fail is Actions billing (0 steps). Code gate on open PRs is green locally; harden python spawn to sys.executable. |

## Email evidence

Gmail: dozens of `[Netie-AI/Cortex] PR run failed` / `Run failed: CI - main`.
Annotation on every job:

> The job was not started because recent account payments have failed or your
> spending limit needs to be increased.

Jobs complete in ~2s with `steps: []` and empty `runner_name`.

## Six-vector audit (PR #85 primary)

| Vector | Verdict |
|--------|---------|
| 1 Syntax & formatting | PASS — ruff clean |
| 2 Config yaml/json/Docker | PASS — no config churn; `INVARIANT-CHANGE:` present |
| 3 Dependencies | PASS — no package changes |
| 4 Env vars | PASS — no new `os.environ` / secrets |
| 5 Test coverage | PASS — 82 contract tests; new R-0007 synthetic gate |
| 6 Error handling | PASS — assertions fail closed |

Local: `pytest tests/contract/` 82 passed; `lint-imports` 2 kept.

## Secondary PRs

- **#84** dms-ui CVE pin (`next@14.2.35` + overrides) — code OK; blocked by billing.
- **#82** `CORTEX_CONTRACT_WHEEL` set in workflow (not a secret) — code OK.
- **#70** night_shift WIP — merge after billing; review Dockerfile/env separately.

## Code fix shipped this session

1. `CortexOS/execution/app_runner._render_argv` rewrites bare `python`/`python3`
   to `sys.executable`. Fixes `start_spawn:[Errno 2] ... 'python'`.
2. Regenerated `contract/openapi-1.2.0.json` (+ sha256) for additive
   `QueryObjectsRequest.session_id` — would have failed `export_openapi.py
   --check` the moment billing cleared.

Local mirror (2026-08-28): ruff/mypy/lint-imports/versions/secrets/base/
protected-paths PASS; pytest **1474 passed**; **rls-proof PASS** on local
Postgres 16 (`test_rls_blocks_out_of_scope_read`). Artifact:
`/opt/cursor/artifacts/cicd-local-gate-report.md`.

Repo is **private** — no free public Actions minutes. Billing is mandatory.
Billing unblock email sent to oojianhongg@gmail.com.

## Unblock

1. https://github.com/organizations/Netie-AI/settings/billing
2. Fix payment or raise Actions spending limit
3. Re-run workflows on `main`, then open PRs (#86 first)
