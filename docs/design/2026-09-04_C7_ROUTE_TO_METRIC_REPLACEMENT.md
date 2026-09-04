# C7 — replace `route_to_metric` keyword cascade

**Date:** 2026-09-04  
**Epic:** Netie-AI/Cortex#17 EPIC-006 (founder override superseding #12)  
**Branch / worktree:** `cursor/c7-design-epic-006` at `D:\Cortex-wt\c7-design`  
**This ticket ships:** design + C7-01 shadow mode only. Do not rewrite the cascade here.

C7 in CLAUDE.md: replace the keyword cascade in `answer_engine.route_to_metric` with schema retrieval → generation → sqlglot → EXPLAIN → bounded retry → plausibility. L2 already exists as an env-gated port. The serve path stays L0/L1 until numeric gates pass.

---

## (a) Current state (measured 2026-09-04 against `bf4ecee`)

### Router 2 order

`docs/dms/ROUTER_STATES.md` (established 2026-07-27): session → certified L0 → governed_metric L1 (`route_to_metric`) → query_skill → L2 freeform (`DMS_L2_ENABLED`, default OFF) → L3 abstain. First match wins.

### File:line anchors

| Piece | Path | Lines |
|---|---|---|
| L0/L1/L2 docstring | `CortexOS/dms/answer_engine.py` | 6–13 |
| Slot regex helpers used by L1 | same | `_explicit_limit` 141–161, `_direction` 164–165, `_threshold` 168–170, `_threshold_op` 173–176, `_days` 179–186, `_wants_aggregate` 189–196, `_calendar_month` 199–205, `_pct` 208–210, `_rank_window` 351–380, `_wants_sales_rank` 561–572 |
| Shape refusal (not a metric) | same | `_shape_refusal` 641–643, called 664–665 and again in `answer` 1397–1399 |
| **L1 cascade** | same | `route_to_metric` **647–776** |
| Serve path L0 then L1 then skill then L2 | same | `answer` 1388–1485 |
| L2 port + `attempt_l2` | `CortexOS/dms/l2_generation.py` | port 17–30, register 40–61, `attempt_l2` 90–162 |
| sqlglot + EXPLAIN + retry | `CortexOS/dms/sql_validate_gate.py` | `run_gate` 58–100, `gate_with_retry` 103–134 |
| Manifest enforcer | `CortexOS/execution/manifest.py` | `enforce_manifest` 960+ |
| Enforce then EXPLAIN then fetch | `CortexOS/execution/submit.py` | `execute_sql` 174–228 |
| Schema retrieval | `packs/dms/generative/schema_retrieval.py` | `retrieve` 99+ |
| FreeRoute generator | `packs/dms/generative/sql_generator.py` | `is_configured` 23–32, `generate_candidates` 139–169 |
| Literal normalize (G4) | `packs/dms/generative/literal_normalize.py` | `normalize_sql_literals` 48+ |
| Pack adapter (C2 inversion) | `packs/dms/generative/l2_adapter.py` | 8–37 |
| Pack registers port | `packs/dms/__init__.py` | `register_engine_seams` 17–30 |
| Existing L2 tests | `tests/dms/test_c7_full_generation.py`, `tests/dms/test_sql_validate_gate.py` | — |
| `test_l2_pipelines.py` | lakehouse pipelines, **not** NL→SQL L2 | — |

`DMS_L2_ENABLED` default OFF: `l2_generation.py:95`, `sql_generator.py:25`. When ON and no model, `attempt_l2` returns reason `no verified answer path (L2 not wired)` (`l2_generation.py:111–112`). `ROUTER_STATES.md:67` still says the flag only changes the abstain reason; that is stale — `test_c7_full_generation.py:87–107` already mocks a generator and can serve `layer=generated` / `badge=L2_VALIDATED`.

### Measured counts (`route_to_metric` body 647–776)

| Metric | Count | Notes |
|---|---|---|
| `re.search(` call sites | **27** | One site in the status loop (729–733) runs per token |
| `return _metric_plan(...)` branches | **25** | 23 unique `metric_id`s; `revenue_total` and `revenue_last_month` each have two return sites |
| `return None` | **2** | shape refusal 664–665; fallthrough 776 |
| File-wide `re.search(` in `answer_engine.py` | **65** | includes slot extractors, session anaphora, subject parse; not all are L1 |

Unique L1 metric ids (23): `active_alerts`, `arriving_window`, `avg_lead_time_by_country`, `capacity_above`, `capacity_utilisation`, `cold_storage_count`, `cold_storage_list`, `count_by_carrier`, `count_by_destination`, `expired_count`, `expired_items`, `expired_last_month`, `free_capacity`, `low_stock`, `revenue_last_month`, `revenue_total`, `revenue_windowed`, `sales_by_value`, `sales_by_volume`, `shipments_by_status`, `sku_count`, `stale_restock`, `suppliers_by_risk`.

### What already exists vs what C7 still lacks

Present (gated, not the serve default): reduced-schema retrieval, FreeRoute generation, literal normalize, sqlglot `validate_sql`, optional DuckDB EXPLAIN, bounded retry (`max_retries=2` → 3 attempts), promotion after 5 validated uses.

Missing on the **serve** path: plausibility / abstain-confidence; held-out (non-team) corpus; shadow comparison vs L1; **manifest enforcer inside the L2 generate gate**.

### Gap the replacement must close

`attempt_l2` (`l2_generation.py:126–137`) opens a warehouse connection for EXPLAIN only when `verified is None`. When a session manifest is present, `gate_with_retry(..., con=None)` is parse-only (`sql_validate_gate.py:75–81` sets `explain_ok=True` without EXPLAIN). Manifest runs later in `answer` via `execute_sql` → `enforce_manifest` then EXPLAIN (`submit.py:194–213`). The generate/retry loop can therefore accept SQL the enforcer will later refuse, and it can EXPLAIN SQL the enforcer has not rewritten. C7-02 must put `enforce_manifest` on **every** generated candidate **before** EXPLAIN. No stage may weaken `manifest.py` refusals.

C2: `l2_generation.py` must not statically import `packs.*` (already tested). `answer_engine.route_to_metric` and `_tables_stated_by_metric` still import `packs.dms.semantic` — pre-C2 debt. The replacement must call L2 only through `CortexOS.dms.l2_generation`.

---

## (b) Target pipeline

Every generated SQL is a candidate, not an answer, until the enforcer and EXPLAIN pass. Stages are fail-closed. Served customer envelope fields that gates assert on: `answer` text, `rows`, `layer`, `badge`, `route`, `sql_used`, `assumptions` / reason, `suggestions` (abstain), and when grounded: `grant_kind` / refused vs abstain (F40).

| # | Stage | Input | Output | Refusal type (envelope) | May weaken manifest? |
|---|---|---|---|---|---|
| 1 | Schema retrieval | question | reduced `{tables, columns, joins}` top-k | `schema_empty` / `schema_retrieval_failed` → L3 abstain | no (no SQL yet) |
| 2 | Generation | question + reduced schema + prior violations | 1 candidate SELECT (literal-normalized) | `not_wired`, `leave_machine_denied`, `no_candidate`, `unresolved_literal` (G4) | no — empty list, never a guessed filter |
| 3 | sqlglot allowlist | candidate SQL + semantic layer | `safe_sql` or violations | `PARSE_ERROR`, `MULTI_STATEMENT`, unknown table/column, DDL/DML | no |
| 4 | **Manifest enforcer** | `safe_sql` + same `VerifiedManifest` / grant as the served turn | rewritten SQL or `ManifestError.code` | `refused` + code (`statement_not_allowed`, `path_not_allowed`, `sql_not_analyzable`, …) — **not** Badge.SESSION | **MUST run. MUST NOT be skipped when `verified` is set. MUST NOT be narrowed to make a candidate pass.** |
| 5 | EXPLAIN | post-enforce SQL + read-only con | ok / `EXPLAIN_FAILED:…` | feed violation to retry; exhaust → `SqlGateAbstain` | no — EXPLAIN is after enforce so it sees the rewritten statement |
| 6 | Bounded retry | prior violations | new candidate or exhaust | same as 2–5; `max_retries=2` (3 attempts) stays | no |
| 7 | Execute (C4 submit) | post-enforce SQL | rows | execute `SqlGateAbstain` / `ManifestError` → abstain / refused | no |
| 8 | Plausibility (c) | question + rows + SQL + schema | pass or abstain | `implausible_empty`, `implausible_shape`, `low_confidence` | no — abstain, never rewrite SQL around the enforcer |

L0 certified and L1 governed templates remain the high-trust serve path until (f) passes. Pipeline is the L2 path, then the L1 replacement.

---

## (c) Abstain-confidence / plausibility

After execute, before synthesize. Does **not** generate SQL. Does **not** call the enforcer. Can only pass through or abstain.

Signals (all fail-closed; any trip → abstain, badge `abstain`, no rows):

1. **Empty-success:** 0 rows and the question asserted existence / ranking / threshold. Prefer abstain over a green empty table (G4 class).
2. **Shape:** scalar question (`how many` / `average`) with a listing, or listing with a single unlabeled number and no group key.
3. **Retrieval miss:** generated SQL's tables disjoint from retrieval top-k (wrong-table valid SQL — the BIRD-width failure).
4. **Literal leftover:** a filter literal that value-dict would have rewritten (`BETA` vs `SKU-BETA`) slipped through.
5. **Numeric gate:** optional score in `[0,1]`; default cut **0.55**. Below → `low_confidence`. Score is diagnostic; it must not override 1–4.

Calibration lives on the held-out corpus (d/e), not on team paraphrases of `metrics.yaml`.

---

## (d) Shadow mode (`DMS_L2_SHADOW=1`) — C7-01

After `_done` stamps the **served** envelope, if `DMS_L2_SHADOW` is on:

1. Do not mutate the served dict. Do not change `answer` / `rows` / `layer` / `badge` / `route`.
2. Skip `generated` / `blocked` / `rag` / `catalog` (L2 already served, or not the cascade).
3. Call `attempt_l2(question, verified=verified, force=True, promote=False)` — same manifest/grant as the turn; **never** write promotion / steward rows.
4. If L2 returns SQL, execute with the same grant (`execute_sql` when `verified` else legacy `guard_and_execute`).
5. Append one JSONL line. Path: `DMS_L2_SHADOW_PATH` or `data/engine/l2_shadow.jsonl` via `CortexOS.paths.data_path`. Create parents. `data/engine/` is gitignored.
6. Outer `try/except`: disk, L2, execute — never raise into the serve path.

`DMS_L2_SHADOW` does **not** set `DMS_L2_ENABLED`. Serve L2 still requires `DMS_L2_ENABLED`. Pack `is_configured()` treats SHADOW as configured so a wired model can run in shadow without serving.

Fields: `question`, `served_layer`, `served_badge`, `served_row_count`, `served_values` (≤3 rows or null), `l2_sql` or `l2_refusal_type`, `l2_row_count`, `l2_values`, `agree` (bool), `latency_ms`.

`agree`: both empty/abstain → true; one empty → false; else row counts equal.

Shadow is **sync** and adds latency to the request. Flag is opt-in. Do not enable on the S1 watcher hot path (`docs/research/findings/S1_TOKEN_BUDGET.md`).

---

## (e) Held-out corpus (not team paraphrases)

`bench/accuracy` (36 gold) and `bench/paraphrase` (85) are **development** sets. They were written next to the 23 metrics. They must not be the cutover corpus.

| Split | Source | Use |
|---|---|---|
| Dev | existing golden + paraphrase | keep L1 from regressing while shadowing |
| Held-out SQL | BIRD-SQL + Spider 2.0 style questions over **this** warehouse schema (or a frozen dump), gold SQL from a **different** model/lab than FreeRoute, then human-checked | execution accuracy |
| Must-abstain | adversarial + false-premise from a **different** model (not the serve generator): unknown entities, 1997 Berlin, ESG+weather, destructive phrasing, unanswerable joins | 100% abstain |
| Width | same questions at 20 / 100 / 500 table catalogs (synthetic wide schema) | retrieval miss |

Scoring buckets (`docs/dms/DMS_EVAL_AND_STRESS_PLAN.md` §3.2): correct / abstained / incorrect. Incorrect is the number that must approach zero. A third party writes must-abstain items; the team only reviews, does not author the paraphrases.

---

## (f) Decision procedure (numeric)

All gates are on the **customer envelope** (rendered `answer` + `rows` + badge), not on generated SQL alone.

| Gate | Number | Fail action |
|---|---|---|
| G-abs | must-abstain recall **= 100%** (0 served rows on that split) | no serve-path swap |
| G-err | held-out **incorrect < 2%** and **≤** current L1 incorrect on the same items | no swap |
| G-env | 0 G4-class: success badge + empty/wrong encoding (`BETA` vs `SKU-BETA`) | no swap |
| G-man | `tests/test_execution` green; **0** hostile-corpus reclassifications to `allow_but_predicate_must_apply` | no swap; never weaken `manifest.py` |
| G-sh | ≥ **500** shadow lines on live/dev traffic; report L2-only-correct vs L1-only-correct; no silent serve change | needed before C7-05 |
| G-lat | serve p95 with SHADOW off **unchanged**; SHADOW-on extra latency documented, not a serve gate | ops only |

Cutover (C7-05): `DMS_L2_ENABLED=1` may serve when L0/L1 miss **after** G-abs, G-err, G-env, G-man, G-sh. Retire `route_to_metric` (C7-06) only when L2-as-L1-replacement beats L1 on G-err **and** G-abs still holds. Until then the 27-regex cascade stays.

---

## (g) Sub-tickets (paste-ready)

### C7-01 — L2 shadow JSONL (`DMS_L2_SHADOW=1`)

**WHEN** an operator sets `DMS_L2_SHADOW=1`  
**SHALL** the engine leave the served envelope byte-identical to SHADOW off (same `answer`, `rows`, `layer`, `badge`, `route`) and append one JSONL record under `data/engine/` (path overridable) comparing served vs L2; L2 exceptions SHALL NOT change the envelope.  
**Appetite:** S  
**Files:** `CortexOS/dms/l2_generation.py`, `CortexOS/dms/answer_engine.py`, `packs/dms/generative/sql_generator.py`, `tests/dms/test_c7_l2_shadow.py`  
**Blocked-by:** none (this ticket)

### C7-02 — Manifest before EXPLAIN on every L2 candidate

**WHEN** L2 generates SQL for a grounded session  
**SHALL** `enforce_manifest` run on that SQL before EXPLAIN; a `ManifestError` SHALL surface as customer `route/layer/badge=refused` (not SESSION) and SHALL abort retry of that candidate; EXPLAIN SHALL see only post-enforce SQL. No refusal in `manifest.py` may be narrowed to pass a candidate.  
**Appetite:** S  
**Files:** `CortexOS/dms/sql_validate_gate.py`, `CortexOS/dms/l2_generation.py`, `CortexOS/execution/submit.py`, `tests/dms/test_sql_validate_gate.py`, `tests/test_execution/`  
**Blocked-by:** none (parallel with C7-01)

### C7-03 — Plausibility / abstain-confidence stage

**WHEN** L2 execute returns rows  
**SHALL** a plausibility stage run before synthesize; trips SHALL abstain (`badge=abstain`, empty `rows`, reason in `assumptions`) and SHALL NOT rewrite SQL or skip the enforcer. Empty-success and wrong-table-vs-retrieval SHALL abstain.  
**Appetite:** M  
**Files:** new `CortexOS/dms/l2_plausibility.py` (engine), tests under `tests/dms/`  
**Blocked-by:** C7-02 (stage order: enforce → EXPLAIN → execute → plausibility)

### C7-04 — Held-out corpus + eval harness

**WHEN** C7 cutover is proposed  
**SHALL** a frozen held-out set exist that is not team-written paraphrases of `metrics.yaml`: BIRD/Spider-style items + must-abstain from a different model; the harness SHALL score correct / abstained / incorrect on the customer envelope.  
**Appetite:** M  
**Files:** `bench/` (new split), `docs/dms/DMS_EVAL_AND_STRESS_PLAN.md` (pointer only if needed — do not edit STATUS/CHANGELOG/INDEX in this epic's first ticket)  
**Blocked-by:** none (parallel). Required before C7-05.

### C7-05 — Serve L2 on L0/L1 miss after numeric gates

**WHEN** G-abs, G-err, G-env, G-man, G-sh all pass on the held-out report  
**SHALL** `DMS_L2_ENABLED=1` serve `layer=generated` / `badge=L2_VALIDATED` only after the full pipeline including plausibility; SHADOW-off serve p95 SHALL not regress.  
**Appetite:** L  
**Files:** `CortexOS/dms/answer_engine.py` (call site already exists), pack generator, eval report  
**Blocked-by:** C7-01, C7-02, C7-03, C7-04

### C7-06 — Retire `route_to_metric` cascade

**WHEN** the pipeline as L1 replacement beats L1 on G-err and still holds G-abs  
**SHALL** `route_to_metric` stop being the serve chooser; keyword helpers may remain as slot extractors only if still needed for L0. A commit that deletes the 25 `_metric_plan` branches SHALL include the held-out numbers in the body.  
**Appetite:** L  
**Files:** `CortexOS/dms/answer_engine.py` 647–776, `tests/dms/test_q2_answer_engine.py`  
**Blocked-by:** C7-05
