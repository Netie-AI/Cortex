# DMS Changelog

Agents append one section per shipped feature. Sequential build log.

## F1 — Postgres ledger hardening — 2026-06-26

- **Migration:** `packs/dms/sql/002_ledger_postgres.sql` — append-only trigger, advisory lock docs
- **Backend:** `packs/dms/audit/ledger.py` — dual backend (SQLite default, Postgres via `DMS_LEDGER_DSN`)
- **Serialization:** `pg_advisory_xact_lock` + `FOR UPDATE` on tail row (Postgres); `BEGIN IMMEDIATE` (SQLite)
- **Tests:** `tests/dms/test_f1_ledger.py` (3 SQLite + 3 Postgres skip-without-DSN)

## F2 — Governed chat foundation — 2026-06-26

- **Backend:** `packs/dms/chat/threads.py` — `create_thread`, `append_message`, SQLite `dms_threads` / `dms_messages`
- **Ledger:** `thread.created` on thread create; `message.inbound` on inbound append
- **Migration:** `packs/dms/sql/004_chat_v0.sql` (Postgres + RLS)
- **API:** `CortexOS/api/chat_routes.py` — `POST /dms/threads`, `POST/GET /dms/threads/{id}/messages`
- **UI:** `demo/dms-ui/app/chat/page.jsx` + Sidebar CHAT nav
- **Tests:** `tests/dms/test_f2_chat.py` (3 tests)

## F7 — Security hardening (minimal) — 2026-06-26

- **PII:** `packs/dms/security/pii.py` — detect + `redact_for_prompt` → `[REDACTED:type]`
- **Crypto:** `packs/dms/security/crypto.py` — AES-256-GCM envelope (`encrypt_field` / `decrypt_field`), master key from `DMS_MASTER_KEY` (32-byte base64)
- **RLS:** `packs/dms/sql/003_rls_policies.sql` — steward vs viewer role policies on warehouse tables + audit ledger
- **Choke-point:** `CortexOS/dms/query_service.py` — `_build_nl_query_prompt` wraps all NL query model input
- **Tests:** `tests/dms/test_f7_security.py` (3 tests)
- **Debt (not in scope):** SOPS+age secrets management; token-bucket rate limiting on `/dms/inbox` and auth endpoints

## V1 — Dimensioning + free-space — 2026-06-26

- **Backend:** `packs/dms/vision/dimension.py` (reference-marker + lidar paths, `VISION_MODEL`/`DEPTH_SOURCE` placeholders, generation-model guard)
- **Backend:** `packs/dms/vision/space.py` (occupied/free volume per bin)
- **Store:** `warehouse_store.py` — `get_location_by_id`, `update_item_dims`, dims JSON parse
- **Intake:** suggested dims on intake; `confirm_item_dims` writes fact + ledger `item.dimensioned`; oversize requires `gate_approved`
- **API:** `POST /dms/items/estimate-dims`, `POST /dms/items/{id}/confirm-dims`, `GET /dms/locations/{id}/space`
- **UI:** warehouse page — confirm-dims step after intake; bin free-space display
- **Tests:** `tests/dms/test_v1_dimension.py` (4 tests)
- **Smoke:** `pytest tests/dms/test_v1_dimension.py -q` + full suite 77 passed, 1 skipped

## V0 — Warehouse spine — 2026-06-25

- **Tables:** `dms_locations`, `dms_items`, `dms_movements` (+ F1 `dms_audit_ledger` in SQLite ops DB)
- **Migration:** `packs/dms/sql/001_warehouse_v0.sql` (Postgres RLS policies)
- **Backend:** `packs/dms/vision/` (locations, intake, movement, warehouse_store)
- **F1:** `packs/dms/audit/ledger.py` (hash-chained append + verify)
- **F7:** `packs/dms/security/photo_sanitize.py` (EXIF GPS strip)
- **API:** `CortexOS/api/warehouse_routes.py` — intake, scan-move, location tree, QR PNG label
- **UI:** `demo/dms-ui/app/warehouse/page.jsx`
- **Tests:** `tests/dms/test_v0_warehouse.py` (6 tests)
- **Deps:** `qrcode`, `pillow` in `[dms]` extra
- **Smoke:** `pytest tests/dms/test_v0_warehouse.py -q` + full suite 73 passed

## Debt (named — do not skip)

- **Postgres ledger CI:** Run F1 Postgres tests in CI with `DMS_LEDGER_DSN` or manual gate proof
- **F7 remainder:** SOPS+age secrets; token-bucket rate limiting on inbox/auth
- **Ontology (P1):** Condition not met — see PARKING_LOT.md

## Governance — 2026-06-25

- Files: `docs/`, `.cursor/rules/`, `.cursor/skills/`, `.cursor/AGENTS.md`, `.cursor/hooks/`
- Repo reorganized; planning docs under `docs/dms/`
- Smoke: DMS tests pass (`pytest tests/test_dms/`)
