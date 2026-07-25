# DMS Changelog

Agents append one section per shipped feature. Sequential build log.

## CI/branch hygiene — 2026-07-25

- **Branches:** deleted remote `feat/context-engineering` (merged) and `netie-engine`
  (obsolete checkpoint; content already on `main`).
- **Workflows:** `test.yml` / `secrets.yml` / `rls.yml` triggers now `main` only
  (removed dead `dms-integrated-engine` / `dms-v2` branch filters).
- **CI status:** tip of `main` green (Test + Secrets Scan + RLS Proof).

## B3 — F7 remainder hardening — 2026-07-22

- **CI:** `.github/workflows/rls.yml`, `.github/workflows/secrets.yml`, secrets step in `test.yml`
- **Hooks:** `.githooks/pre-commit` + `scripts/install_git_hooks.ps1`
- **Deps:** `psycopg[binary]` on `[postgres]`; `[agents]` for DBOS
- **Reversible wire:** `secure_message` / classify / NL prompt → `secure_reversible`
- **RBAC:** brain + memory + engine; RLS GUC stamp from `Caller` via `set_rls_context`
- **C-SEC-8:** stripped `__future__` from 7 FastAPI route modules
- **Intake:** guard `depth_map` + size budget on estimate-dims
- **Tests:** `tests/dms/test_b3_f7_remainder.py`

## B1 S1 — DBOS durable resume (smallest) — 2026-07-22

- **Resume path:** `packs/dms/agents/employee.py` — detect / draft / publish / reject as
  durable steps; ops-DB checkpoints always (`dms_agent_run_steps` + `workflow_id` /
  `last_step` on `dms_agent_runs`). Re-enter with same `workflow_id` after draft does
  **not** re-draft; `approve_run` publish is idempotent (one `report.md`).
- **Optional DBOS:** `[project.optional-dependencies] agents = ["dbos>=2.28.0,<3"]`
  (poetry extra `agents` mirrored). `packs/dms/agents/dbos_runtime.py` configures SQLite
  system DB, `run_admin_server=False` under pytest / `DBOS_RUN_ADMIN_SERVER=0`. Without
  `dbos`, sync path + ops checkpoints still resume.
- **Anti-scope held:** no Temporal; no autonomous publish; detectors stay LLM-free.
- **Test:** `test_workflow_resume_after_kill` unskipped (ops-DB + optional DBOS destroy/
  relaunch). `@agent` chat dispatch remains skipped (B2).

## F8 — Tool-call vertical slice (export_pptx) — 2026-07-22

- **Host shim:** `packs/dms/actions/export_pptx.py` — stdlib `zipfile` minimal OOXML PPTX
  (`[Content_Types].xml` + `ppt/slides/slide1.xml` title from params). No `python-pptx`.
- **Runner:** `CortexOS/execution/tool_runner.py` — `ALLOWLIST={"export_pptx"}`, param sanitize
  via `injection_guard` + `pii.redact_for_prompt`, compliance
  `packs/dms/compliance/tool_call_rules_v1.yaml` (title required), writes only under
  `outputs/<actor>/<run_id>/`, ledger `action.tool_call` / `action.tool_call_denied`.
- **DAG:** `dag_runner.execute_tool_call_node` — `TOOL_CALL` no longer raises
  `UnsupportedDAGNodeKind`.
- **API:** `POST /dms/actions/{tool}` steward+; `GET /dms/actions` viewer+ — registered in
  `app.py`.
- **Tests:** `tests/dms/test_tool_call.py` — allowlist deny, viewer 403, steward success +
  ledger, path escape deny.

## Claude Code security C-SEC-1..8 — 2026-07-22

- **C-SEC-1 reversible PII:** `packs/dms/security/reversible.py` — `secure_reversible()`
  composes the audited harness ∘ TokenVault, flag-gated (`DMS_REVERSIBLE_PII`). Flag off =
  byte-identical to the one-way gate; blocked input never creates a vault; regex floor still
  catches what a blind detector misses. `tests/security/test_secure_reversible.py` (8).
- **C-SEC-2 RLS proof:** `packs/dms/sql/007_rls_ledger_force.sql` (FORCE RLS) +
  `tests/dms/test_rls_blocks_out_of_scope_read.py` (skips ≠ pass without `DMS_LEDGER_DSN`) +
  `docs/security/RLS_PROOF.md` (CI sketch). Viewer cannot read steward-only / foreign-tenant rows.
- **C-SEC-3 SOPS + scanner:** `scripts/secrets_scan.py` (`--staged`), `.sops.yaml`,
  `secrets/dms.env.example.yaml`, `.gitignore` hardening. Clean tree = 0 findings;
  planted secrets caught. `tests/security/test_secrets_scan.py` (4).
- **C-SEC-4 filetype choke:** `packs/dms/security/intake_policy.py` wires `filetype_guard`
  before photo + ingest (`ingest_routes`, `loader`, `vision/intake`, `warehouse_routes`);
  exe-as-csv/png rejected before disk. `tests/security/test_intake_filetype_wiring.py` (5).
- **C-SEC-5 crypto/transport memo:** `docs/security/CRYPTO_TRANSPORT_MEMO.md` — AEAD accept
  w/ key-separation contract; egress-allowlist gap + default-deny design.
- **C-SEC-6 WASM honesty:** `tests/security/test_wasm_honesty.py` — kill-switch, no-WASI,
  fuel accounting proven; adversarial modules honestly skipped (scaffold ≠ Firecracker).
- **C-SEC-7 publish rail:** `tests/dms/test_agent_publish_rail.py` — detectors have no LLM;
  no publish after reject; below-bound never fires.
- **C-SEC-8 __future__ sweep:** ranked 10-module fix list for Cursor (mechanical).
- **NEVER-TOUCH choke-points untouched; adversarial suite green before/after.** Suite: 260
  passed, 8 skipped. Hand-back: `docs/dms/packets/CLAUDE_CODE_SECURITY_HANDBACK_2026-07-22.md`.

## Research wave A1–A5 + truth-ground — 2026-07-22

- **Findings (parallel explore):** `docs/research/findings/{S1_DBOS_RESUME,S2_BROKER_SHORTLIST,B1_STRESS_SUITE,S1_TOKEN_BUDGET,P0_SECURITY_GAPS}.md` + `P0_INDEX.md`
- **Truth map:** `docs/dms/TRUTH_GROUND_MAP.md` — feature→file→test→state + cross-app links
- **Claude Code packet:** `docs/dms/packets/CLAUDE_CODE_SECURITY_PACKET.md` — high-dimension security (RLS, SOPS, vault wire, WASM)
- **ARCHITECTURE.md** updated: Q1/Q2/L1/L2/S0/S1-core marked shipped; remainders listed honest
- **Verdicts:** DBOS for S1 resume; NATS JetStream for S2 V1; detect=0 LLM forever

## S1 smoke + stream stress re-verify — 2026-07-22

- **S1 tests:** `tests/dms/test_s1_agents.py` — detector pure SQL, approval gate, publish artifact, ledger chain, API RBAC (DBOS resume + @agent chat deferred/skipped)
- **Stream stress:** `python -m bench.stress --scenario stream` → ~379 ev/s @ 8 threads, 0 errors (buffer concurrency fix holds)
- **Q2 bridge:** narrow legacy fallback for `\b(delayed|late)\b` ranked queries; abstain copy restores "DMS semantic layer"
- **Synced:** fast-forwarded `dms-integrated-engine` to wave1 pull (`4fb30ee`)

## Integrate — dms-v2 + engine/lakehouse — 2026-07-21

- **Base:** latest `origin/dms-v2` (F6 PASS + F7 RBAC + portable demo)
- **Ported from `netie-engine-up`:** engine registry, memory plane, AirGPT sidecar, L0 DuckLake, accuracy bench
- **From `dms-f6-phase0`:** `docs/dms/PHASE0_PLAN.md`, honest `ARCHITECTURE.md` (F3–F6 Shipped)
- **App:** `/api/engine/*`, `/api/memory/*`, `/dms/lakehouse/*`, sidecar routes wired in `app.py`
- **Note:** chat UI remains F2 governed threads (+ F5 gate); engine APIs are live for AirGPT/hosting
- **Tests:** accuracy core xfail until alert column cleaning (P6); suite otherwise green

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
