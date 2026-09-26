# EPIC-GRANT-01: session grant catalog store, GET/POST /crew/session-grants (#202)

- **Date:** 2026-09-23
- **Keywords:** crew, grant, session-grants, catalog, folder, window, office_file, persist, uacc, #202
- **Main idea:** Crew records an Allow / Cancel decision for named laptop folders, already-open windows and named Office files before any reach happens. The store holds only the three catalog kinds; a drive root, POSIX `/`, a UNC share root, the profile root, a `..` path or a device prefix is refused with 4xx and the store is byte-identical afterwards. `persist` unset is session-only (process memory keyed by `session_id`); `persist=true` writes `data/crew/session_grants.json`. Nothing reaches the laptop; jail expansion is EPIC-GRANT-02.
- **Verify:** `python -m pytest tests/test_crew/test_session_grants.py tests/test_crew tests/contract -q -p no:cacheprovider`
- **Does not prove:** live UACC `list_windows` liveness of a pid; a real Windows filesystem (normalisation is string-level via `PureWindowsPath` / `PurePosixPath`); `python scripts/export_openapi.py --check` (needs `.[full]` extras that are not installed here; crew routes are not in `CONTRACT_ROUTE_IDS`, so no drift is expected).
- **Cite:** Cortex#202 EPIC-GRANT-01. Store convention from `CortexOS/crew/approvals.py` (`approvals.json`, session grants die with the process). Route mount convention from `liberty_routes.py` / `prompt_harness_routes.py`. Did not edit `workspace.py`, `policy.py`, `contract/`, `tests/contract`, `tests/invariants`, `.importlinter`.

## Expected vs actual

| | Expected | Actual |
|---|---|---|
| Store shape | Only folder / window / office_file rows | `KINDS` frozenset; any other `kind` is 400 and writes nothing |
| Drive root | `C:\` refused, store unchanged | `PureWindowsPath("C:\\").parts == ("C:\\",)`; zero parts after the anchor is refused (403). `C:/`, `C:`, `D:\` also refused. Parametrised test asserts the persisted file bytes and the GET list are unchanged after each refusal |
| Profile root | `USERPROFILE` / `HOME` refused | Both env vars are read; `SessionGrantBook(profile_roots=[...])` injects for tests. Case-insensitive on Windows-style paths, exact on POSIX. Subfolders of the profile are allowed (that is the point of the grant) |
| persist unset | session-only | Row goes to `_session[sid]`, file is never created, a fresh `SessionGrantBook` on the same path sees nothing |
| persist true | survives a new book | Row goes to `_persisted[sid]` and `session_grants.json` (0600, same as approvals) |
| Window | title + pid from UACC `list_windows` | Non-empty title, positive int pid (bool refused). `persist=true` on a window is refused (400): a pid does not outlive the process, so persisting one would be an invented grant |
| Office file | named file under a folder granted allow in this session | Extension in `.docx .xlsx .pptx .doc .xls .ppt .csv` (case-insensitive) and one of its parents equals a folder with decision `allow` in the same session (session-only or persisted). A cancelled folder admits nothing. Another session's folder admits nothing |
| Cross-session | no leak | GET returns only rows whose `session_id` matches; `s3` with no grants returns `[]` |

## Repro

```
POST /crew/session-grants {"session_id":"s1","kind":"folder","path":"C:\\"}
-> 403 {"detail":"a drive root (C:\\) is not a grantable folder"}
GET  /crew/session-grants?session_id=s1
-> {"ok":true,"session_id":"s1","grants":[],"count":0,"reach":false,"law":"..."}
```

## Root-cause class

New surface, no defect fixed. One self-inflicted flake caught by the full suite: the first cut ordered rows by second-resolution `created_at` then random id, so two grants recorded inside one second could swap order and the test asserting `["folder","office_file"]` failed only under the full run. Replaced with a monotonic per-book `seq` (stripped from responses and from the file).

## Invariant applied

- Fail closed: any path the normaliser cannot prove is a real sub-folder is refused (relative, drive-relative `C:foo`, `\\?\` and `\\.\` prefixes, `//?/`, UNC server or share root, NUL byte, over 1024 chars).
- Refusal before write: `_build` validates fully under the lock before the row list is touched, so a refused POST cannot leave a half-written row.
- Stale file cannot smuggle: `_load` drops rows that are not a catalog kind, have no identity or path, or are `window` (never persisted).
- `CortexOS/**` imports no `packs.*`; `lint-imports` 3 kept, 0 broken; `tests/contract` 95 passed.

## Verified vs assumed

- Verified: 49 new tests green three times in a row and inside the full suite; `tests/test_crew` + `tests/contract` 490 passed; `ruff check CortexOS tests/test_crew` reports only the pre-existing I001 in `tests/test_crew/test_approvals.py` (not touched); `mypy` 41 errors in 26 files, none in `session_grants.py` or `server.py` (pre-existing); `lint-imports` 3 kept; `python scripts/check_versions.py` ok.
- Assumed: no contract version bump. The new endpoints are Crew sidecar routes, not `cortex_contract` models, and `scripts/export_openapi.py` exports only `CONTRACT_ROUTE_IDS`. The `--check` run itself could not execute here (missing `qdrant_client`, `tantivy`, `sentence_transformers`).
- Not done on purpose: no jail expansion, no UACC call, no auto-launch, no all-apps kind. `workspace.py` and `policy.py` untouched.
