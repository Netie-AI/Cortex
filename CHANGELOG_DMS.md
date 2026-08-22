# DMS Changelog

Agents append one section per shipped feature. Sequential build log.

## Unbound session must abstain — 2026-08-22

A session with no binding was answering from the demo warehouse on
`POST /dms/query` (badge `governed_metric`, `grant_kind local-self-issued`,
all six demo tables granted). The table-intersect never fired because the
self-issued grant already contained every table.

- `route_to_metric` now returns the tables its plan will read (from the metric
  definition, not by re-parsing compiled SQL).
- Served doors (`/dms/query`, `/mcp/call`) fail closed when nothing is bound
  or the grant is self-issued. They do not mint a Space grant.
- Bound demo-table questions still answer (R-0005). A bound abstain names the
  sources the session *can* answer over.
- Rewrote `test_unbound_session_still_answers_and_says_the_grant_is_self_issued`
  to expect abstain. Cortex#36 "total" routing and Cortex#39 (customers -> SKUs)
  are untouched.
- C2: L2 generation no longer imports `packs.dms.generative` from
  `answer_engine`. The engine holds `L2GenerationPort`; `attempt_l2` lives on
  that seam. Pack registers the adapter. No C2 ignore added.

## Router audit — generalization benchmark, routing fixes, hot-path perf — 2026-07-27

**New measurement.** `bench/paraphrase.py` + `bench/golden/dms_paraphrase_v1.yaml`:
85 ordinary paraphrases of the 36 golden intents, scored against the same canonical
SQL. `bench.accuracy` cannot detect brittleness — the L1 router is hand-written
regex, so it passes its own phrasings by construction. Baseline was **23.5%**
robustness; now **64.7%**, with **0 confidently wrong** answers (100% answered
precision) and golden still 36/36.

**Routing fixes (a bad classifier failing in both directions):**
- `destructive_intent()` replaces `\b(drop|delete|…|update|create)\b` over raw
  English. It refused `"update me on the delayed shipments"` and `"cost by
  drop-off point"`, and missed `"wipe all supplier records"`, `"remove the
  inventory table"`, `"erase everything in inventory"`. Now SQL-statement shapes
  + (mutation verb → data object), benign idioms stripped first, copula-preceded
  verbs ignored; refusals carry an auditable cause. Enforcement is unchanged —
  `sql_guardrail`'s sqlglot AST check was always the real gate.
- `RAG_KEYWORDS` fired on the bare openers `what does` / `explain`, so analytics
  questions were answered from the supplier-contract corpus. Now requires a
  document noun.
- Dead branch: `\b(utilis|utiliz|how full|usage)\b` could not match the word
  "utilisation" (trailing `\b` fails before "ation"). Hidden because the golden
  question hits L0 certified. Now `utilis\w*`.
- `packs/dms/semantic/vocabulary.py` (new) — business phrasing → router
  vocabulary in front of L1, the idea already declared as `synonyms:` on every
  metric and read by nothing. **Slots stay on the original question**, so a
  rewrite of the words can never move a number, a threshold or a direction;
  asserted by test.

**Perf — 85% of query latency was overhead, not work.** Single-thread
**1090 ms → 80 ms/query (13.6×)**; 8-thread **3.4 → 38.5 q/s (11.3×)**, p50
**1694 → 95 ms**, p95 **6048 → 727 ms**, errors 2 → 0. `pytest tests/dms`
339 s → 156 s.
- fresh DuckDB connection per question (~500 ms) → one cached read-only instance
  per process, cursor per caller. A read-write open **evicts** the cached reader
  first — DuckDB refuses two connections to one file with different
  configurations, and without eviction the cached reader locks the writer out of
  its own process (caught by the suite, fixed).
- `CREATE TABLE IF NOT EXISTS` + `CREATE INDEX` script on **every** query
  (~440 ms, maintaining an index that is never read) → once per process
- `Path.resolve()` / `mkdir()` per query → once
- `table_row_counts` / `preview_table` are pure reads and no longer take the
  write lock

**Root cause found for the documented flaky golden benchmark.** `STATUS.md`
recorded the cause as unestablished and suspected `packs/data/dms_ops.db`
(SQLite). It is `data/dms_demo.duckdb`: DuckDB takes an **exclusive** file lock
for read-write connections, so a live API process locks the benchmark out —
`IO Error: … used by another process. File is already open in … (PID 27532)`.
`get_connection(..., read_only=True)` + `DMS_READ_ONLY_QUERIES` fixes it (3
consecutive clean runs with the server up). The same flag is the prerequisite for
running more than one reader process — writer caveat documented in the audit.

**Docs:** `docs/dms/ROUTER_STATES.md` (complete two-router state map, live-probed,
including the finding that `query_skill` has 42 stored skills and 0 retrievals),
`docs/dms/FOUNDATION_AUDIT_2026-07-27.md` (layer-by-layer vs Databricks/Snowflake,
measured; the lakehouse is `built` but holds zero tables and the answer engine
does not read from it).

**Tests:** `test_destructive_intent.py` (30 cases), `test_vocabulary_normalization.py`
(invariants: comparisons, directions, numbers and entities must survive
normalization). Suite **668 passed, 6 skipped, 0 failures** with
`DMS_READ_ONLY_QUERIES` both ON and OFF.

## Seek learning UI + G2.3 Claude handoff — 2026-07-27

- Seek UI: `value_why`, learned chip, Accept/Dismiss → `/outcome`, history, Not audited
- Proxies: `/api/cortex/goals/{id}/outcome`, `/values`
- G2.2 acknowledged SHIPPED; next Claude locked to G2.3 OSR
  `docs/dms/packets/CURSOR_TO_CLAUDE_G2_3_OSR_2026-07-27.md`
- `NEXT_LANES.md` refreshed

## Seek UI + G2.2 Claude handoff — 2026-07-26

- AirGPT Platform **Seek** page: bind `EnterpriseGoal` → Seek now → assumptions + proposals
- Proxies: `/api/cortex/goals*`, `/api/engine/seek` (+ cortex_client helpers)
- G2.0/G2.1 acknowledged SHIPPED (Claude); next packet
  `docs/dms/packets/CURSOR_TO_CLAUDE_G2_2_ACTION_VALUE_2026-07-26.md`
- Standing continue file: `docs/dms/packets/NEXT_LANES.md`

## Cursor lane — Routines/Apps UI + G2 Claude handoff — 2026-07-26

- AirGPT: Routines page → Cortex draft/preview/Create (`/api/cortex/routines*`)
- AirGPT: Apps hub Cortex packages → `about` / `explained_reasons` / Dockerize (`/api/cortex/apps*`)
- `cortex_client` helpers + clipdrop proxies (namespaced away from AirGPT `/api/apps` ports)
- Removed redundant `monkeypatch.chdir` from DMS route fixtures
- Claude build packet: `docs/dms/packets/CURSOR_TO_CLAUDE_G2_SEEK_2026-07-26.md` (G2.0/G2.1)

## G2 plan — enterprise gen-cFSM loop — 2026-07-26

- **Plan only (no runtime):** `docs/strategy/ENTERPRISE_GEN_CFSM_LOOP_PLAN.md`
- **Key idea:** proactive-first (actively seek ethical enterprise goal; reactive ingress secondary)
- Open-set interrupt path + JEPA action-value + DAG gen + ActionEvent compress/uplink +
  pattern-armed assist + signed update port / minimal OAuth
- Wired: `CORTEX_FINAL_GOAL.md`, PARKING_LOT **P21**, `STATUS.md`, `P0_INDEX.md` G2,
  G1 continuation, master-plan H3 bridge
- **Next code when owner asks:** G2.0 → G2.1 (seeker + silence litmus)

## Oracle-scale E0 — engine unlock + memory/A2A/MCP — 2026-07-24

- **A1 Autostart:** `scripts/install_engine_autostart.ps1`, hardened `start_cortex_engine.ps1` (pid/url hint, DryRun, port 8010); AirGPT `cortex_client.ensure_engine` / `spawn_engine` with `CORTEX_AUTOSPAWN`
- **A2 Run plan:** `CortexOS/execution/run_plan.py` + `POST /api/engine/run` — dispatches dag/rag/memory/ontology; marketplace → `adapter_unavailable`
- **A3 RAG DAG:** `RAG_RETRIEVE` / `RAG_RERANK` / `RAG_ANSWER` nodes + Modular-RAG templates (basic/high/max) in `CortexOS/rag/{lexical,templates}.py`
- **A4 Memory:** `factory.get_store` (RawKnn), `MemoryContextProvider`, `semantic_cache`
- **A5 A2A:** in-process runtime + `POST /a2a/messages`; DAG `A2A_CALL` executor
- **A6 MCP (read-only):** `GET /mcp/tools`, `POST /mcp/call` — answer_engine / lakehouse / agent.status
- **AirGPT B1–B4:** optional rerank (`AIRGPT_RERANK=1`), adaptive depth from benchmark, sqlite-vec install helper, citation chips + files-surfed + stream-guarded confirms

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
