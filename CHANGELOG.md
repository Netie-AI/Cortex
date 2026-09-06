# CHANGELOG.md
Append-only. Older DMS-era notes: `CHANGELOG_DMS.md`.

## 2026-09-05 -- open PRs is not open a PR

- `scheduled_work` `pr_create` no longer matches "open PRs". Review
  diffs use a 20s gh timeout. Desk `GH_WAIT_S` stays 1.5s.

## 2026-09-05 -- Gmail IMAP connector for scheduled digest

- `inbox.fetch(timeout=)` for scheduled sweeps (desk stays 1.5s).
  App-password spaces stripped. Crew keys file stays gitignored.
  Google account passwords fail with `Application-specific password required`.

## 2026-09-05 -- scheduled_work FreeRoute brief

- After gh/ship-gate/mail facts, one OpenVault `model=auto` completion
  prepends a summary. Unit tests inject the briefer; `CORTEX_SCHEDULED_BRIEF=0`
  skips live calls. Operator text stays if the vault is down.

## 2026-09-05 -- scheduled gh timeout 20s

- Operator `scheduled_work` PR list now calls `github.list_prs(timeout=20)`
  so a CLAIMS sweep is not `TimeoutExpired` at the 1.5s chat budget.
  Desk `GH_WAIT_S` stays 1.5s.

## 2026-09-05 -- scheduled_work: gh / ship-gate / mail

- Routine dispatch for GitHub, review, ship-gate, and email goals runs
  Crew `gh` + `ship_gate` + IMAP/SMTP instead of echoing the prompt on
  the minimal DAG. `gh pr create` never merges. Crew `inbox.py` still
  does not send; the engine may SMTP with Gmail app password.
- Tests: `tests/dms/test_scheduled_work.py` plus Crew gh diff/create.

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
