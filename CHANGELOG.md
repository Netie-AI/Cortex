# CHANGELOG.md
Append-only. Older DMS-era notes: `CHANGELOG_DMS.md`.

## 2026-09-03 -- docs honesty (recon #9) + lakehouse env

- LOOP-01/02 + SEC-01 re-verified (11 passed). `:8011` `/health` 200.
- Contract release table now matches packaged `1.2.0`.
- STATUS: one current header; test baseline floor >=330.
- WASM: live map/packet no longer cite deleted `wasm_isolate.py`.
- `warehouse_path` re-reads `DMS_WAREHOUSE_DB` so L0 migrate does not lock the live warehouse.
- Gate: `tests/security/test_docs_honesty.py` + L0 migrate isolation.

## 2026-08-28 -- LOOP + SEC-01

- AGENT_TASK quality stop (LOOP-01/02, EPIC-016).
- POST `/dms/query` through `enforce_manifest` (SEC-01 PathNotAllowed envelope).
