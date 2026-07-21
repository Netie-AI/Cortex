# BUILD PLAN V2 — LAKEHOUSE + 99% ANSWER ENGINE + STREAMING AGENTS + DATA STUDIO
**Cursor-ready master plan. One feature per session, in order. Prove each, then the next.**

**Owner decision 2026-07-20:** parking-lot items P3, P6, P12 (and the F8 packet) are ACTIVATED
into this plan; P1 partially (O1–O3 plumbing); P2/P4/P7/P9/P10/P11/P13 stay condition-gated
(see §8). Branch: build on `netie-engine-up` (merge to `dms-v2` later — no conflict expected;
this plan only adds modules).

**Research anchors (read before building the matching track — verdicts are binding):**
- `docs/research/findings/LAKEHOUSE_2026.md` — DuckLake 1.0 core, SQLite→Postgres catalog, Iceberg exit ramp
- `docs/research/findings/NL2SQL_ACCURACY_2026.md` — 4-layer answer engine, verified queries, penalty eval
- `docs/research/findings/STREAMING_ORCH_2026.md` — V0/V1/V2 streaming tiers, DBOS durable execution, agent-on-stream

**Shipped this session (2026-07-20, do not rebuild):**
- `bench/accuracy.py` + `bench/golden/dms_golden_v1.yaml` + `tests/dms/test_accuracy_benchmark.py`
  — baseline: **core 18/18 (100%), safety 4/4, target 0/14** (the target tier IS the Q2 backlog)
- `bench/stress.py` v0 — ledger append storm (chain valid @ ~200 appends/s) + NL→SQL concurrency (p95 ~56ms @ 6 threads)
- `build_chart_spec` crash fix (non-numeric column no longer crashes alert queries)

---

## 0. What the owner asked for → what ships

| Ask | Track | Feature |
|---|---|---|
| "DMS as strong as Databricks behind a chat interface" | L | L0–L4 lakehouse core |
| "Lakehouse replacing datalake/warehouse split, no migration" | L | L0 (one DuckLake catalog, bronze/silver/gold schemas) |
| "~99% accuracy, never wrong numbers for Salesforce/Oracle users" | Q | Q1–Q3 layered answer engine + golden benchmark |
| "SQL AI agent — ask and it outputs SQL" | Q | Q2 (always shows SQL + provenance) |
| "Genie-like agent, manager calls it like an employee" | S | S1 watcher/report agents + approvals |
| "Auto-streams from Kafka/Flink/sensors" | S | S0 webhooks → S2 Kafka-compatible tier |
| "Serverless or choose-your-cluster" | L/S | in-process DuckDB = serverless-by-architecture; Docker backends = "cluster" (E-series engine registry already models this) |
| "Data engineer / analyst / MLE / AI engineer on one management page" | U | U0 Data Studio |
| "Strong data sharing across the team" | U | U1 company brain (P12) with role-labelled memory + RLS |
| "Benchmarking + stress testing" | B | B0 shipped, B1 full suite |

## 1. Target architecture

```
                      ┌────────────────────────────────────────────┐
 chat / Studio UI ───►│  Q2 ANSWER ENGINE (4 layers, abstain-safe) │──► answer + SQL + badge + snapshot_id
                      └───────┬────────────────────────────────────┘
                              │ reads governed views only
 ┌─────────────┐   ┌──────────▼──────────┐   ┌──────────────────┐
 │ S0-S2 stream │──►│  L0 LAKEHOUSE       │◄──│ L1 ingest        │◄─ files / CSV / Excel
 │ webhooks,    │   │  DuckLake catalog   │   │ (Auto Loader     │   Salesforce/Oracle sync (L4)
 │ NATS, Kafka  │   │  bronze|silver|gold │   │  analog)         │
 └──────┬──────┘   │  ACID · time travel │   └──────────────────┘
        │           └──────────┬──────────┘
 ┌──────▼──────────┐          │ L2 declarative pipelines + expectations (quarantine, event log)
 │ S1 watcher      │   ┌──────▼──────────┐
 │ agents (DBOS    │   │ L3 catalog +    │    F1 ledger under EVERYTHING (hash-chained)
 │ durable, F5-    │   │ lineage (sqlglot│    F5 compliance gate on every action
 │ gated, human    │   │ + ops DB edges) │    F7 RBAC/RLS/PII on every surface
 │ approve)        │   └─────────────────┘
 └─────────────────┘
        U0 DATA STUDIO: Catalog | Pipelines | Quality | Agents | Benchmarks | Audit (one page, role-aware)
```

## 2. Sequencing (do not reorder without a reason written to STATUS.md)

```
L0 lakehouse ─► Q1 semantic layer v2 ─► Q2 answer engine ─► L1 ingest ─► L2 pipelines/expectations
     └─► S0 stream intake ─► F8 tool-call execution (existing packet) ─► S1 watcher agents
U0 studio shell (after Q2)   L3 catalog/lineage (after L2)   B1 stress suite (after S0)
L4 interop/export · S2 broker tier · U1 company brain · O1–O3 ontology  = wave 3
```

Gate discipline unchanged: every feature → its smoke test + `pytest -q` green + `python -m bench.accuracy`
core/safety still 100% + CHANGELOG_DMS.md entry. `SUPERVISOR_GATE.md` applies.

---

## FEATURE L0 — Lakehouse foundation (DuckLake core)

```
CONTEXT
Cortex repo, branch netie-engine-up. Read docs/research/findings/LAKEHOUSE_2026.md first — its
verdicts are binding. Today analytics live in a plain DuckDB file (data/dms_demo.duckdb) loaded
from CSVs by CortexOS/dms/warehouse_db.py. This feature adds a true lakehouse under the DMS:
one open format (Parquet + SQL catalog), ACID, time travel, schema evolution — local-first, $0.

GOAL
A DuckLake-backed lakehouse at data/lakehouse/ with bronze/silver/gold schemas, a Python API
(packs/dms/lakehouse/), FastAPI routes, and the demo warehouse data flowing through it.
The existing query path keeps working unchanged.

BUILD EXACTLY THIS
1. Deps: bump `duckdb>=1.5.2` in pyproject [dms] extra (DuckLake v1.0 requires it; 1.5.4 is installed).
2. packs/dms/lakehouse/catalog.py:
   - attach(catalog_path=data/lakehouse/catalog.sqlite, data_path=data/lakehouse/data/) using
     `INSTALL ducklake; LOAD ducklake;` + ATTACH 'ducklake:sqlite:<path>' (verify exact syntax
     against https://ducklake.select docs at build time; SQLite catalog = multi-process local).
   - ensure_schemas(): bronze, silver, gold schemas inside the lake catalog.
   - graceful_fallback: if the ducklake extension cannot install (offline CI), fall back to plain
     DuckDB schemas in data/lakehouse/fallback.duckdb and set capability flag
     lakehouse_mode="ducklake"|"fallback" (surfaced in /api/engine/specs). Tests must pass in BOTH modes.
3. packs/dms/lakehouse/tables.py:
   - write_table(schema, name, rows_or_df|SELECT), append_rows, read(schema, name) -> relation
   - snapshots(schema, name) -> list[{snapshot_id, ts, operation}]  (DuckLake snapshot metadata)
   - query_at(snapshot_id, sql) — time-travel read (ducklake AT (VERSION => …) syntax)
   - evolve guard: adding a column is allowed; dropping/renaming requires explicit flag.
4. Seed migration script scripts/lakehouse_migrate.py:
   - bronze.<table>_raw  ← the 6 *_messy.csv files, all-VARCHAR permissive + _rescued JSON column + _ingest_meta (file, mtime, content_hash, loaded_at)
   - silver.<table>      ← the 6 *_clean.csv files with typed columns (same casts as warehouse_db._cast_types)
   - gold.sales_by_sku, gold.capacity_by_location, gold.supplier_risk — the 3 ranked-query shapes from query_service as materialized gold tables.
   - keep data/dms_demo.duckdb loading exactly as today (query_service untouched this feature).
5. CortexOS/api/lakehouse_routes.py: GET /dms/lakehouse/tables, GET .../tables/{schema}/{name}/snapshots,
   POST .../query {sql, snapshot_id?} (route through the EXISTING sqlglot guardrail — read-only, allowlist
   extended to lake schemas), all behind F7 API-key RBAC (viewer read, steward write).
6. Ledger: append 'lakehouse.table_written' / 'lakehouse.migrated' events (F1 ledger, actor from API key).

ANTI-SCOPE — DO NOT
- Do not rewire CortexOS/dms/query_service.py or warehouse_db.py yet (Q2 does reads-through-views).
- No Delta/Iceberg/Unity Catalog/Polaris embedding. Iceberg is L4 EXPORT only.
- No Spark, no JVM, no cloud object store. Local paths only (S3 support = config placeholder).
- Do not break the 166-passing baseline or bench core/safety 100%.

ACCEPTANCE CRITERIA
- Round-trip: write silver table → query → matches source CSV row count.
- Time travel: write v1, update rows, query_at(v1) returns pre-update data (ducklake mode).
- Schema evolution: add a column, old snapshots still readable.
- Fallback mode: with ducklake unavailable, same API works (no time travel; flag says so honestly).
- All 6 demo tables present in silver via scripts/lakehouse_migrate.py; bronze holds messy raw.

SMOKE TEST (tests/dms/test_l0_lakehouse.py)
- test_attach_and_schemas · test_write_read_roundtrip · test_snapshots_and_time_travel (skip in fallback)
- test_fallback_mode_works · test_migration_seeds_all_tables · test_guardrail_blocks_ddl_via_api
Run: pytest tests/dms/test_l0_lakehouse.py -q AND pytest -q AND python -m bench.accuracy (core/safety 100%)

ON DONE: CHANGELOG_DMS.md entry; STATUS.md active-feature update.
```

---

## FEATURE Q1 — Semantic layer v2: metrics, certified queries, value dictionaries

```
CONTEXT
Read docs/research/findings/NL2SQL_ACCURACY_2026.md first — binding. The accuracy contract lives
in bench/golden/dms_golden_v1.yaml (core 18/18 today; target tier = 14 known gaps). The current
packs/dms/semantic_layer.yaml (glossary/joins/tables) is load-bearing for F5/F8 — DO NOT change
its existing keys. This feature builds the governed-metrics layer the answer engine (Q2) compiles
against: the Snowflake-Cortex-Analyst / Databricks-Genie "trusted assets" pattern.

GOAL
Three new governed artifacts + loaders + auto-built value dictionaries, and the golden set grown
to 120+ questions. No answer-path behavior change yet (Q2 consumes this).

BUILD EXACTLY THIS
1. packs/dms/semantic/metrics.yaml — every metric the DMS can answer, e.g.:
   - id: sales_value | sql_template: SELECT sku, ROUND(SUM(quantity_kg*unit_cost_myr),2) AS sales_value_myr
     FROM transactions WHERE txn_type='OUT' {date_filter} GROUP BY sku ORDER BY sales_value_myr {direction}, sku ASC LIMIT {limit}
   - params with types + defaults + allowed ranges (limit int 1..1000 default 5; direction ASC|DESC; date_filter optional)
   - synonyms: [revenue, sales, sold value, turnover]
   - Cover AT MINIMUM every glossary term in semantic_layer.yaml plus every target-tier golden gap:
     risk_above_threshold, count_by_carrier(status), count_by_destination(status), avg_lead_time_by_country,
     revenue_windowed(days), free_capacity, cold_storage_count, scalar counts for every listing metric,
     truncation-safe listing (returns total_count + top-N sample).
2. packs/dms/semantic/certified_queries.yaml — verified-query repository:
   {id, question, sql, verified_by, verified_at, tags}. Seed it with all 18 core golden items
   (they are literally verified). Loader validates each SQL with sqlglot + executes it once at load (must not error).
3. packs/dms/semantic/values.py — value dictionaries: for each categorical column
   (category, status, carrier, country, location_code, severity, txn_type, supplier_name)
   SELECT DISTINCT capped at 200 values, cached with refresh(); fuzzy resolve(entity_text, column)
   -> (value, confidence). PII columns (semantic_layer.yaml sensitive_columns) are EXCLUDED.
4. packs/dms/semantic/loader.py — load_all() merging v1 semantic_layer.yaml (read-only) + metrics
   + certified + values into one SemanticModel dataclass. Validation errors fail loudly at import.
5. Golden set v2 (bench/golden/dms_golden_v2.yaml): grow to 120+ items per the research recipe —
   every metric × 2-3 phrasings, every join path, 15-20% adversarial/unanswerable/ambiguous
   (match: abstain), keep v1 ids stable. bench/accuracy.py gets --golden PATH (default stays v1
   until Q2 flips it).

ANTI-SCOPE — DO NOT
- Do not modify semantic_layer.yaml existing keys (additive sibling files only).
- Do not change query_service.py routing/answers (Q2's job).
- No LLM calls anywhere in this feature — it is pure governed data.
- Do not let a certified query or metric template interpolate raw strings (params bind via
  validated whitelist substitution only — sqlglot-parse the compiled SQL and re-verify allowlist).

ACCEPTANCE CRITERIA
- load_all() returns a model with ≥15 metrics, ≥18 certified queries, value dicts for ≥6 columns.
- Every metric template compiles with defaults + parses via sqlglot + executes read-only.
- resolve("Chemicals", category)→'CHEMICALS'; resolve("warehouse A", location_code)→'WH-A'.
- Golden v2 loads, ids unique, ≥120 items, ≥18 abstain/blocked items.

SMOKE TEST (tests/dms/test_q1_semantic_v2.py)
- test_load_all_validates · test_metric_templates_compile_and_execute · test_param_injection_blocked
- test_value_resolution · test_certified_seed_matches_core_golden · test_golden_v2_shape
Run: feature test + pytest -q + bench core/safety 100%.

ON DONE: CHANGELOG_DMS.md.
```

---

## FEATURE Q2 — Layered answer engine (the 99% program)

```
CONTEXT
Read docs/research/findings/NL2SQL_ACCURACY_2026.md — the 4-layer verdict is the spec. Q1 shipped
the semantic model. bench/golden target tier documents today's 14 failures: dead keyword branches
("score" hijacks risk-filter questions, "delayed" hijacks carrier counts), silent LIMIT truncation
(1031 delayed rows shown as 100), scalar questions answered with listings, and the DEFAULT_INVENTORY_SQL
fallback that delivers confident wrong listings for unmapped questions. This feature replaces the
heuristic core of CortexOS/dms/query_service.py with a layered engine that NEVER delivers an
unverified answer.

GOAL
bench/accuracy.py on golden v2: answered-precision ≥99%, zero confident-wrong, target tier of v1
fully green, core stays 100%. Every answer carries {sql, layer, badge, snapshot/provenance, assumptions}.

BUILD EXACTLY THIS
1. CortexOS/dms/answer_engine.py — answer(question) pipeline, first layer that fires wins:
   L0 CERTIFIED: exact/normalized/embedding match against certified_queries (BGE embedder already
      in stack; cosine ≥ 0.92 = hit, else miss). Deterministic replay of stored SQL. badge="certified".
   L1 METRICS: intent+slot extraction — rules first (reuse/port the good branches from generate_sql
      as slot patterns), optional T0/T1 LLM slot-filler behind LLM_SLOT_FILLER flag (default off).
      Slots resolve via values.resolve; ANY unresolved/ambiguous material slot (metric, date range,
      location scope, unit) → L3 clarify with the assumption named. Compile SQL deterministically
      from the metric template. badge="governed_metric".
   L2 VERIFIED FREE-FORM (flag DMS_L2_ENABLED, default OFF until a model is wired):
      T1 model generates 3-5 candidates → sqlglot parse + allowlist → execute all → result-set
      majority vote → sanity rails (non-empty unless truth is empty; aggregates within 10x of
      metric-layer estimate when comparable; date ranges echoed) → badge="generated_verify".
      Any disagreement → L3.
   L3 ABSTAIN/CLARIFY: never DEFAULT_INVENTORY_SQL. Return route="needs_clarification" +
      2-3 nearest answerable questions from certified/metrics catalog (embedding similarity).
2. Truncation honesty: any listing hitting the guard cap runs a COUNT(*) companion query; the
   answer text states "showing N of TOTAL" and chart uses TOTAL; scalar questions ("how many…")
   compile to COUNT metrics, never listings.
3. Rewire answer_question() to call answer_engine.answer(); keep response schema backward-compatible
   (all existing keys) + new keys {layer, badge, assumptions, total_count}. route/blocked/rag behavior
   unchanged. The PII choke-point (_build_nl_query_prompt) wraps ALL text that reaches any model.
4. Flip bench default golden to v2; move v1 target tier expectations to gated
   (tests/dms/test_accuracy_benchmark.py: target tier now asserts wrong==0 too; abstain allowed).
5. Ledger: 'answer.delivered' {layer, badge, sql_hash, row_count} per answer.

ANTI-SCOPE — DO NOT
- No auto-enabled cloud LLM in the hot path: L0/L1 are deterministic; L2 ships flag-off.
- Do not weaken sqlglot guardrails, PII choke-point, or RBAC.
- Do not delete query_service helpers other code imports (synthesize_answer, build_chart_spec stay).
- Do not chase coverage by loosening gates — coverage grows only by adding certified/metric entries.

ACCEPTANCE CRITERIA
- python -m bench.accuracy --golden bench/golden/dms_golden_v2.yaml:
  answered_precision ≥ 0.99, confident-wrong == 0, core+v1-target all correct, abstain rate ≤ 15%.
- "Which suppliers have a risk score above 0.7?" → the 8 correct rows (dead branch resolved).
- "Which shipments are delayed?" → discloses 1031 total.
- "Which supplier gave us the best price last quarter?" → clarify + suggestions (no listing).
- Every sql-route answer includes sql_used + layer + badge.

SMOKE TEST (tests/dms/test_q2_answer_engine.py)
- test_certified_layer_hits · test_metric_slot_compile · test_unresolved_slot_clarifies
- test_no_default_fallback_listing · test_truncation_disclosed · test_scalar_questions_get_counts
- test_layer_and_badge_in_response · test_l2_flag_off_by_default
Run: feature + pytest -q + bench v2 gate above.

ON DONE: CHANGELOG_DMS.md; STATUS.md headline metric update.
```

---

## FEATURE L1 — Ingest: Auto Loader analog (P6 activation, part 1)

```
CONTEXT
LAKEHOUSE_2026.md §5 verdict: file-ledger incremental ingestion. L0 exists. Today CSVs load via
warehouse_db.py manually. Goal: drop a file in a folder (or POST it) → exactly-once into bronze.

BUILD EXACTLY THIS
1. packs/dms/ingest/loader.py: file ledger table lake.bronze._ingest_ledger
   {path, size, mtime, content_hash, status(pending|loaded|failed|skipped_duplicate), loaded_at, error}.
   scan(folder) diffs ledger vs disk; load_one(path): schema-infer via read_csv_auto/read_json_auto
   into bronze.<stem>_raw with permissive VARCHAR + _rescued + _ingest_meta; content-hash dedup.
2. Watch mode: polling watcher (5s default, stdlib only — no watchdog dep) behind
   scripts/ingest_watch.ps1; plus POST /dms/ingest/upload (steward RBAC, size cap, EXIF strip for images
   via existing photo_sanitize).
3. Excel: .xlsx via openpyxl→rows (already a dep). JSONL supported.
4. Every load → ledger event 'ingest.loaded' {path, rows, content_hash}; failures quarantined to
   status=failed with error, never partial-committed (write to temp table, atomic swap).
ANTI-SCOPE: no agentic cleaning yet (L2); no Salesforce/Oracle connectors (L4); no broker (S-track).
ACCEPTANCE: same file twice → one load + one skipped_duplicate; corrupt CSV → failed, no partial rows;
  10k-row CSV lands in bronze < 5s.
SMOKE (tests/dms/test_l1_ingest.py): test_exactly_once · test_corrupt_quarantined · test_excel_and_jsonl
  · test_upload_rbac_and_ledger. Full suite + bench green.
ON DONE: CHANGELOG_DMS.md.
```

---

## FEATURE L2 — Declarative pipelines + expectations (P6 activation, part 2)

```
CONTEXT
LAKEHOUSE_2026.md §5 (Lakeflow/DLT expectations) + existing compliance engine philosophy
(deterministic YAML→checks; LLM proposes, rules decide, human approves). L0+L1 exist.

BUILD EXACTLY THIS
1. packs/dms/pipelines/defs/*.yaml — declarative bronze→silver→gold:
   {source, target, transform_sql, expectations: [{name, constraint_sql, action: warn|drop|fail}]}.
2. packs/dms/pipelines/runner.py: run(pipeline) executes transform inside one lake transaction;
   expectations evaluate per-row where possible; action=drop → rows to silver.<t>_quarantine with
   reason; action=fail → abort + ledger 'pipeline.failed'; metrics (rows in/out/dropped/warned)
   into lake._pipeline_events (the DLT event-log analog).
3. Agentic cleaning (the P6 promise, governed): packs/dms/pipelines/propose.py — given bronze
   profile (null rates, type-parse failures, near-duplicate keys via exact+fuzzy match), the LLM
   proposes cleaning rules AS YAML expectations/transforms (T1, PII-choked). Proposals land in
   defs/proposed/ with status=pending; steward approves via API/Studio → moves into the pipeline.
   LLM NEVER mutates data directly. (Splink entity-resolution = later toggle; exact-dup dedup now.)
4. The 6 demo pipelines: messy CSVs → silver tables, with expectations reproducing _cast_types +
   sanity (quantity_kg ≥ 0, risk_score in [0,1], valid FK to locations/suppliers → else quarantine).
5. Nightly gold refresh reuses runner (gold.sales_by_sku etc.).
ANTI-SCOPE: no dbt/great-expectations dependency (their patterns, our engine — keep deps zero);
  no auto-approved proposals; no cross-DB federation.
ACCEPTANCE: messy→silver run quarantines bad rows with reasons; row counts reconcile
  (in == out + dropped); re-run is idempotent; a failing 'fail' expectation blocks and ledgers.
SMOKE (tests/dms/test_l2_pipelines.py): test_expectations_warn_drop_fail · test_quarantine_reasons
  · test_idempotent_rerun · test_proposal_requires_approval · test_event_log_metrics.
ON DONE: CHANGELOG_DMS.md; PARKING_LOT.md P6 marked SHIPPED.
```

---

## FEATURE S0 — Stream intake v0 (webhooks → lakehouse)

```
CONTEXT
STREAMING_ORCH_2026.md verdict V0: FastAPI webhooks + asyncio batch writer. No broker. L0 exists.

BUILD EXACTLY THIS
1. CortexOS/api/stream_routes.py: POST /dms/streams/{stream_id}/events (single or batch JSON;
   API-key RBAC; size caps; injection/PII scan on string fields via existing guards).
2. packs/dms/streams/buffer.py: per-stream asyncio queue → batch writer (flush every 2s or 500
   events) → bronze.stream_<id> (schema: ts, event JSON, _ingest_meta). Backpressure: 429 past cap.
3. Stream registry table (ops DB): {stream_id, name, created_by, schema_hint, status} + CRUD routes
   (steward). Ledger 'stream.created' / batched 'stream.flushed' {count}.
4. Simulator: scripts/stream_simulate.py --stream sensors --rate 50 --seconds 60 (temperature/scan
   events) for demos + B1 throughput bench.
ANTI-SCOPE: no NATS/Redpanda/Kafka yet (S2); no agents (S1); no exactly-once claims beyond
  at-least-once + content-hash dedup per batch.
ACCEPTANCE: 50 ev/s for 60s → all events in bronze within 5s of send; malformed events rejected 4xx;
  restart mid-stream loses at most one unflushed batch (documented).
SMOKE (tests/dms/test_s0_streams.py): test_post_and_flush · test_batch_and_backpressure
  · test_registry_rbac · test_malformed_rejected.
ON DONE: CHANGELOG_DMS.md.
```

---

## FEATURE F8 — Tool-call execution
**Already fully specced in `docs/dms/GATE_F8_PACKET.md` — build exactly that packet next after S0.**
Prereq for S1 (agents need governed actions). F7 remainder (Postgres RLS CI + SOPS) rides along per packet.

---

## FEATURE S1 — Watcher agents: the "AI employee" (P3 activation)

```
CONTEXT
STREAMING_ORCH_2026.md verdicts: DBOS Transact for durable execution (SQLite default → Postgres),
deterministic-detector-then-agent pattern, human approval before publish. F8 gives governed tool
calls; S0 gives streams; Q2 gives trustworthy answers; F5 gates actions. This is the Genie-analog:
a manager "hires" an agent that watches data and reports.

BUILD EXACTLY THIS
1. Dep: dbos (MIT) in a new [agents] extra. Workflows persist to ops SQLite (Postgres via DSN).
2. packs/dms/agents/detectors.py — deterministic, cheap, on schedule/window:
   threshold (metric vs bound), delta (vs previous window), staleness (no events for T).
   Detector configs are YAML per agent; evaluation is pure SQL over lakehouse — no LLM.
3. packs/dms/agents/employee.py — DBOS durable workflow per agent run:
   detect → (fired?) → gather context (Q2 answer_engine calls, certified layer only by default)
   → draft report/action (Brain generative templates from packs/dms/generative) → F5 compliance
   gate → status=pending_approval → human approves in Studio/chat → publish (F8 tool call:
   export_pptx / send via P4-stub) → ledger every step. Crash mid-run → resumes from last step.
4. Agent registry (ops DB): {agent_id, name, role_label, watches(stream/table/metric), schedule,
   detector_cfg, report_template, approver_role}. CRUD routes, steward+.
5. Chat surface: "@agent" in DMS chat (F2 threads) → create/inspect/run agents conversationally;
   agent replies post into the thread as the agent actor (clearly labelled non-human).
6. MCP surface (read-only first): expose answer_engine.answer + lakehouse.tables + agent status as
   an MCP server (stdlib/fastmcp) so external runtimes (Claude/Cursor) can call Cortex as tools.
ANTI-SCOPE: no autonomous publish without approval (value_threshold rule from F5 applies);
  no LLM in detectors; no Temporal (documented scale-out path only); no cron daemon — schedules
  tick from the existing orchestrator loop.
ACCEPTANCE: simulated sensor stream breaches threshold → agent drafts report → appears
  pending_approval with F5 verdict → approve → artifact in outputs/<actor>/<run>/ + full ledger
  chain; taskkill /F mid-workflow → rerun resumes, no duplicate report (chaos-lite).
SMOKE (tests/dms/test_s1_agents.py): test_detector_pure_sql · test_workflow_resume_after_kill
  · test_approval_gate_blocks_publish · test_agent_chat_dispatch · test_ledger_chain_complete.
ON DONE: CHANGELOG_DMS.md; PARKING_LOT.md P3 marked SHIPPED (DBOS path).
```

---

## FEATURE U0 — Data Studio: one management page

```
CONTEXT
The owner's core demand: data engineer, data analyst, ML engineer, AI engineer — one management
surface where their data and work connect. demo/dms-ui is Next.js 14 with existing pages
(chat/brain/warehouse/skills). Q2/L2/S1 APIs exist by now.

BUILD EXACTLY THIS
1. demo/dms-ui/app/studio/page.jsx — one page, six tabs (client-side, role-aware via existing
   role switcher): 
   CATALOG: lakehouse tables (schema, rows, snapshots, tags, owner) + preview + time-travel picker.
   PIPELINES: pipeline defs, last runs, event-log metrics, quarantine counts, proposed-rule approvals.
   QUALITY: expectations pass/warn/fail trend, quarantine browser, reconciliation status (L4 later).
   AGENTS: agent registry, runs timeline, pending approvals (approve/reject inline).
   BENCHMARKS: bench/results/accuracy_last_run.json + stress_last_run.json rendered (precision,
   coverage, wrong=0 badge, p95 latencies) + "run benchmark" button (steward).
   AUDIT: existing ledger viewer embedded + verify-chain button.
2. Persona lenses: viewer sees Catalog/Quality/Benchmarks; steward + approvals; admin + audit config.
   Same objects, one page — no per-role silos.
3. New thin API endpoints only where a tab lacks one (GET /dms/bench/latest, GET /dms/pipelines/runs).
ANTI-SCOPE: no new design system (existing tokens; mono for data; inline styles for gradients);
  no websockets (poll 5s); do not fork existing pages — link them.
ACCEPTANCE: every tab renders real data end-to-end on run_demo.ps1; approve flow works from AGENTS
  tab; role switch changes capabilities.
SMOKE: Playwright-less — API-level tests per new endpoint + demo checklist in docs/DEMO.md update.
ON DONE: CHANGELOG_DMS.md.
```

---

## Wave-3 features (spec blocks — expand to full prompts when their turn comes)

### L3 — Catalog, lineage, governance
sqlglot.lineage() over every pipeline transform + answer-engine SQL → edges in ops DB
(table→table, column-level where cheap); OpenLineage-format event emission optional flag; tags +
owners on lake tables; Studio CATALOG tab gains lineage graph. NO Unity Catalog OSS/Polaris embed.

### L4 — Interop: exit ramps + source sync ("Salesforce/Oracle users never get wrong data")
Export: Parquet copy (trivial), Iceberg copy (DuckLake→Iceberg), Delta via deltalake writer if a
client demands. Sync-in: read-only connectors (Postgres/Oracle via SQLAlchemy DSN, Salesforce via
simple REST extract) land in bronze with per-sync RECONCILIATION: source row count + per-column
checksums vs landed data, mismatch → quarantine + alert, never silent. This is the data-engineering
correctness rail the owner asked for.

### S2 — Broker tier
V1: NATS JetStream (native Windows single binary, nats-py) as embedded option; V2: Redpanda
(Docker/WSL2) + Quix Streams for Kafka-API interop with client Kafka/Flink estates. Same bronze
landing contract as S0; consumers are DBOS workflows.

### U1 — Company central brain (P12 activation)
Shared memory scope on netie.memory store (M-series): role-labelled contributions
(research brief §F4), RLS-scoped recall, contribution feed in Studio; every team member's
uploads/notes embed into company scope; Q4-style insights cite memory + lakehouse provenance.

### O1–O3 — Ontology plumbing (P1 partial activation)
Execute docs/ontology/CORTEX_ONTOLOGY_PLAN.md phases O1–O3 only (shared registry over
semantic model + skills + rules + ledger). O6+ (customer-facing AIP claims) stays gated on a
paying client per the plan's own §0.

### B1 — Full stress + chaos suite (extends bench/stress.py)
k6 scripts for /dms/query + /dms/streams (Windows binary, checked into bench/k6/);
ingest throughput bench (rows/s into bronze via S0 simulator); chaos-lite: taskkill /F API +
agent worker mid-run → assert ledger chain valid + DBOS resume + no partial lake commit;
24h soak profile with stubbed LLM; DuckDB concurrency knee finder (degradation curve vs threads);
all results appended to lake.gold.benchmarks table; Studio BENCHMARKS tab reads it.

---

## 8. Parking-lot disposition (owner decision 2026-07-20)

| Item | Disposition |
|---|---|
| P1 ontology | PARTIAL — O1–O3 via this plan (wave 3); O6+ still gated on paying client |
| P2 WASM/Firecracker | GATED (unchanged) — F8 uses wasm_isolate scaffold as specced |
| P3 Temporal/durable | **ACTIVATED → S1 (DBOS verdict)** |
| P4 respond.io endpoint | GATED — S1's draft+approve loop is its substrate; build on DMS partner |
| P6 ingest pipeline | **ACTIVATED → L1+L2** |
| P7 annotation | GATED (needs 50+ real warehouse interactions) |
| P9 respond.io full · P10 FDE playbook · P11 PQC · P13 Web3 | GATED (unchanged conditions) |
| P12 company brain | **ACTIVATED → U1 (wave 3)** |

## 9. Honesty rails (claims discipline)
- Say: "true lakehouse — one open format, ACID, time travel, schema evolution, local-first, zero infra";
  "serverless-by-architecture"; "Iceberg-compatible export"; "certified answers with zero-wrong gate".
- Do not say: Databricks/Delta runtime compatibility; "Palantir parity"; "99% accuracy" until
  bench v2 prints ≥99 answered-precision — then say "≥99% answered-precision with X% abstain, zero
  confident-wrong on our golden benchmark" and show the Studio BENCHMARKS tab.
- Every generated (L2) answer is visibly badged; certified/metric answers show their SQL either way.

## 10. How to run this in Cursor
1. Keep `.cursor/rules` as-is; open planning mode per feature; paste ONE feature block; review plan; execute.
2. Sequential only within a track; L0→Q1→Q2 strictly. After each: feature smoke + `pytest -q` +
   `python -m bench.accuracy` (core/safety must stay 100%) + CHANGELOG entry.
3. If a feature needs a fact this plan lacks → check the three findings files first; if still open,
   STOP and write the question into docs/research/ for the owner instead of guessing.
