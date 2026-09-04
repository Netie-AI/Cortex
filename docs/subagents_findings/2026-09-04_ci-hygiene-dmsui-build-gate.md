# CI hygiene + dms-ui build gate

- Date: 2026-09-04
- Keywords: ci, main-only, dms-ui, next14, dependabot, warehouse_path, auto-merge
- Main idea: Workflows and auto-merge now listen only to `main`. Local `next build` on Node 24/Windows failed EISDIR so `dms-ui-build` was not added. Dependabot majors still fail; warehouse_path tests need engine changes.
- Path: this file

Cite: `2026-08-28_cicd-multipr-gate-audit.md` (React19/Next16/recharts3 FAIL).

## A. CI hygiene

`branches: [main]` on push and pull_request in `.github/workflows/ci.yml`, `rls.yml`, `secrets.yml`.
`ALLOWED_BASES = frozenset({"main"})` in `scripts/auto_merge_if_perfect.py`.
New test: `baseRefName == "dms-v2"` -> `skip`.

`verdict()`: any rollup check with FAILURE/TIMED_OUT/ACTION_REQUIRED/STARTUP_FAILURE returns `skip` before REQUIRED is considered. A failing extra job already blocks merge. REQUIRED is only the wait-for-green tuple. Do not add `dms-ui-build` there.

## B. dms-ui-build job

**Not added.** `npm ci` on a clean prefix succeeded. `npm run build` (Next 14.2.35, Node v24.18.0, Windows) failed verbatim:

```
Error: EISDIR: illegal operation on a directory, readlink '...\node_modules\next\dist\pages\_app.js'
```

`_app.js` is a 1554-byte regular file. CI job would have used ubuntu-latest / Node 22. Local Node 24 is the only runtime on this machine. `next start` then failed: no `.next` production build.

## C. Dependabot (do not merge/close)

See table at end (filled after lockfile builds). Prior audit: swr optional PASS; react 19 / react-dom 19 / recharts 3 / next 16 FAIL.

## D. Visual

Port 3000 was Vite (`D:\DMS-epic020`), not dms-ui. Screenshots retaken at 1280x800 against Next `next dev -p 3011`. Offline/empty states.

| Route | File |
|-------|------|
| `/` | `scratch-screens/home.png` |
| `/audit` | `scratch-screens/audit.png` |
| `/brain` | `scratch-screens/brain.png` |
| `/chat` | `scratch-screens/chat.png` |
| `/data` | `scratch-screens/data.png` |
| `/skills` | `scratch-screens/skills.png` |
| `/studio` | `scratch-screens/studio.png` |
| `/warehouse` | `scratch-screens/warehouse.png` |

`npm run test:e2e`: 4 failed. Engine down; `request.get(http://127.0.0.1:8000/health)` throws ECONNREFUSED before `test.skip(!res.ok())`.

## E. warehouse_path test

Not added. `github/warehouse-path-4abf` needs `FALLBACK_DB`, `WAREHOUSE_DB_ENV`, call-time env re-read, and no second `dms_demo.duckdb` default. Trivial import aliases: 3 passed / 3 failed. Failures:

- `warehouse_path()` ignores late `DMS_WAREHOUSE_DB` (import-time `DEFAULT_DB`)
- reader/writer did not open the env path
- `CortexOS/agent_sdk/backends.py` hardcodes `dms_demo.duckdb`

No engine edits. FF-03 `str(SqlGateAbstain)` does not apply. Cleanup: dropped accidental `path_probe` table on worktree `data/dms_demo.duckdb`.

## F. Verify

- ruff: pass
- lint-imports: 2 kept
- pytest `tests/test_execution tests/security tests/packaging/test_auto_merge_if_perfect.py`: 283 passed
- auto-merge file alone: 8 passed (includes `test_dms_v2_base_is_skip`)
- mypy: fail in shared venv numpy stub (`type` statement vs `python_version = "3.11"` in pyproject). Not this diff.

## Dependabot table

Local Node v24.18.0 Windows. `npm ci` then `npm run build`. HEAD lockfile restored after; restore `npm ci` exit 0.

| ref | bump | result | first real error |
|-----|------|--------|------------------|
| github/dep-swr | swr ^2.5.1 | FAIL | `Error: EISDIR: illegal operation on a directory, readlink '.../next/dist/pages/_app.js'` (same as main Next 14.2.35) |
| github/dep-recharts | recharts ^3.10.1 | FAIL | same EISDIR (masks any recharts-3 compile break) |
| github/dep-react | react ^19.2.8 | FAIL | `npm error code ERESOLVE` — `peer react@"^18.2.0" from next@14.2.35` vs react@19.2.8 |
| github/dep-react-dom | react-dom ^19.2.8 | FAIL | `npm error code ERESOLVE` — `peer react-dom@"^18.2.0" from next@14.2.35` vs react-dom@19.2.8 |
| github/dep-next | next 16.3.2 | PASS | Compiled successfully in 17.6s; 8 app routes prerendered |
