# DMS Changelog

Agents append one section per shipped feature. Sequential build log.

## L0 — Lakehouse foundation (DuckLake) — 2026-07-20 (`netie-engine-up`)

- **Core:** `packs/dms/lakehouse/catalog.py` (attach + schema bootstrap + honest fallback) +
  `tables.py` (write/read/append, snapshots, `AT (VERSION => N)` time travel, additive-only
  evolution guard, identifier/schema allowlist)
- **DuckLake verified on-box:** duckdb 1.5.4, `ducklake` extension installs; SQLite catalog +
  Parquet data at `data/lakehouse/`. Fallback mode (plain DuckDB, no time travel) auto-engages
  air-gapped and reports `lakehouse_mode` honestly — tests run in BOTH modes.
- **Migration:** `scripts/lakehouse_migrate.py` — bronze (messy CSV, permissive VARCHAR +
  `_source_file`/`_loaded_at`) / silver (clean, typed) / gold (sales_by_sku, capacity_by_location
  incl. `free_kg`, supplier_risk). Seeds 6+6+3 tables; ~28k/25k/716 rows.
- **API:** `CortexOS/api/lakehouse_routes.py` (`/dms/lakehouse/{status,tables,.../snapshots,
  .../preview,query}`) — reads through the existing sqlglot guardrail (DDL/DML → 400), F7 RBAC.
  Registered in `api/app.py`.
- **Deps:** `duckdb>=1.5.2` (DuckLake v1.0 floor).
- **Tests:** `tests/dms/test_l0_lakehouse.py` (7 × 2 modes = 14). Full suite **180 passed, 4
  skipped**; bench core/safety still 100%.
- **Naming:** module docstrings adopt the **OpenDMS** product name (see plan); no code-symbol
  mass-rename this pass.

## B0/Q0 — Accuracy benchmark + stress harness v0 + BUILD_PLAN_V2 — 2026-07-20 (`netie-engine-up`)

- **Plan:** `docs/dms/BUILD_PLAN_V2_LAKEHOUSE.md` — lakehouse (DuckLake), 99% answer engine,
  streaming agents, Data Studio; parking-lot P3/P6/P12 activated (P1 partial), see plan §8
- **Research:** `docs/research/findings/{LAKEHOUSE_2026,NL2SQL_ACCURACY_2026,STREAMING_ORCH_2026}.md`
- **Benchmark:** `bench/accuracy.py` + `bench/golden/dms_golden_v1.yaml` (36 items, 3 tiers) +
  `tests/dms/test_accuracy_benchmark.py` — baseline core 18/18 (100%), safety 4/4, target 0/14
  (target tier = machine-checked Q2 backlog: dead keyword branches, silent LIMIT truncation,
  DEFAULT_INVENTORY_SQL confident-wrong fallback)
- **Stress:** `bench/stress.py` — ledger append storm (chain valid, ~200/s) + NL→SQL concurrency
  (p95 ~56ms @ 6 threads); full suite specced as B1
- **Fix:** `query_service.build_chart_spec` no longer crashes on non-numeric value columns
  (alert listings previously raised ValueError end-to-end)
- **Tests:** 166 passed, 4 skipped

## Netie Engine Up — E0/M0 scaffold — 2026-07-19 (`netie-engine-up`)

- **E0 registry:** `CortexOS/engine/registry.py` + `/api/engine/{specs,backends,config}`
- **M0 memory:** `CortexOS/memory/store.py` + `/api/memory/{upsert,query,stats}` (named `memory`, not `brain`)
- **AirGPT sidecar:** `/dms/secure|classify|audit/*` for `cortex_client.py`
- **CORS:** demo UI :3000 + AirGPT :8765
- **Mirrors:** `scripts/sync_mirrors.ps1` + `install_sync_hook.ps1`; start via `scripts/start_cortex_engine.ps1`
- **Tests:** `test_sidecar_routes.py`, `test_engine_routes.py`


## Gate F5 — PASS 2026-07-03

- Supervisor verified: all 6 `test_gate.py` checklist tests by name; F3 classify PII case green
- Local re-run: **141 passed, 4 skipped**; supervisor sandbox 133 passed (ML-dep files excluded)
- Next: **F6** skill capture (consented, opt-in) — see BUILD_PLAN § FEATURE 6

## Gate F6 — PASS 2026-07-03

- Supervisor verified all 4 `test_skill_capture.py` tests by name; ledger events confirmed in code
- Known gap (client `actor`) addressed in F7 remainder RBAC slice
- Next: F7 remainder completion → F8

## F7 remainder (RBAC slice) — 2026-07-03

- **Auth:** `packs/dms/security/api_auth.py` — `X-API-Key` / Bearer → viewer/steward/admin
- **Rate limit:** `packs/dms/security/rate_limit.py` — 429 on `/dms/*` when bucket exceeded
- **Routes:** `skill_routes.py` — steward+ for config/deactivate/complete; actor from key
- **Demo:** UI role switcher maps to demo API keys; `run_demo.ps1` sets `DMS_API_KEYS`
- **Tests:** `tests/dms/test_f7_rbac.py` (8) — **153 passed, 4 skipped** total

## F6 — Skill capture — 2026-07-03

- **Capture:** `packs/dms/skills/capture.py` — opt-in (`DMS_SKILL_CAPTURE_ENABLED`), gate=pass + outcome=success only
- **Schema:** `packs/dms/sql/006_dms_skills_v0.sql` — `dms_skills` + task event outcome columns
- **Suggest boost:** `suggest(state, trigger_text=...)` — `w4*skill_match` from captured skills
- **API:** `CortexOS/api/skill_routes.py` — list, config toggle, complete, deactivate
- **UI:** `demo/dms-ui/app/skills/page.jsx` — steward admin view + capture on/off
- **Ledger:** `skill.captured`, `skill.deactivated` events
- **Tests:** `tests/dms/test_skill_capture.py` (4) — **145 passed, 4 skipped** total

## Gate F4 — PASS 2026-06-26

- Supervisor verified: F4 task suggest, Ponytail, Brain generative routes, 134 tests green
- Next: F5 compliance gate (see section below when shipped)

## F5 — Compliance gate — 2026-06-26

- **Rules:** `packs/dms/compliance/dms_rules_v1.yaml` (quote, pickup, outbound verify)
- **Gate:** `packs/dms/tasks/gate.py` — `check_task()`, value threshold Python post-rule
- **Extract:** `packs/dms/tasks/extract.py` — LLM/heuristic extract only; rules decide
- **Migration:** `packs/dms/sql/005_task_events_v0.sql`
- **API:** `CortexOS/api/task_routes.py` — gate/check, choose, acknowledge
- **PII fix:** `classify()` runs `secure_for_prompt()` before all paths (Gate F4 conditional)
- **UI:** chat verdict banner, brain gate inline; `demo/dms-ui/lib/api.js` restored
- **Tests:** `tests/dms/test_gate.py` (6) — **141 passed, 4 skipped** total

## F4 + Ponytail + Brain — 2026-06-26

- **F4 tasks:** `packs/dms/tasks/suggest.py`, `learn.py` — rule T0 + history T1 + optional LLM T2 rank
- **Ponytail:** `CortexOS/ponytail/middleware.py` — prefetch, compress, route, cache, security gate, ledger
- **Brain:** `packs/dms/generative/brain.py` — chart, CSV, email, WhatsApp, analysis, CEO report
- **API:** `CortexOS/api/brain_routes.py` — `/dms/brain/*` (12 routes)
- **UI:** `demo/dms-ui/app/brain/page.jsx` + Sidebar BRAIN nav
- **Scripts:** `scripts/verify_all.ps1`, `scripts/git_push_all.ps1`
- **Tests:** F4 (12), Ponytail (9), generative (12) — **134 passed, 4 skipped** total
- **CI:** import smoke for F4/Ponytail/Brain in `.github/workflows/test.yml`

## F3 + Security wave — 2026-06-26

- **Classify:** `packs/dms/classify/intent.py` — warehouse intents, sentiment, psychological_state
- **Security:** `injection_guard.py`, `scam_guard.py`, `prompt_harness.py` — pre-model gate
- **WASM:** `CortexOS/execution/wasm_isolate.py` — fuel-limited sandbox
- **Persona:** `packs/dms/persona/profiles.py` — respond.io wedge routing
- **GPU:** `scripts/setup_gpu_env.ps1` — PyTorch cu132 + optional Qwen
- **Tests:** adversarial corpus (15 cases), F3 classify, WASM — 103 passed total
- **Research:** `docs/research/respond_io_analysis.md`

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
