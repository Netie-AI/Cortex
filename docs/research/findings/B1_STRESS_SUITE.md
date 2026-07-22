# B1 — Full stress + chaos suite (design)

**Track:** B1 (BUILD_PLAN_V2 § B1)  
**Status:** RESEARCH ONLY — no code in this doc  
**Date:** 2026-07-22  
**Implements later as:** Cursor workstream **B6** (`docs/dms/CURSOR_EXEC_PACKET_2026-07-22.md`)

Extends `bench/stress.py` v0 (ledger / NL→SQL / stream) into a gated, artifact-producing suite: k6 HTTP load, chaos-lite kill/resume, 24h soak, DuckDB concurrency knee, lake gold benchmarks, Studio BENCHMARKS consumption.

---

## 0. Baseline (truth today)

| Source | Fact |
|---|---|
| `bench/stress.py` | Scenarios: `ledger_append_storm`, `nl_sql_query_concurrency`, `stream_ingest_throughput` |
| `bench/results/stress_last_run.json` | Stream only (2026-07-22): **378.8 ev/s @ 8 threads**, 0 errors; p95 batch latency ~4.3s (flush contention) |
| `CHANGELOG_DMS.md` / `STATUS.md` | Stream stress re-verify ~379 ev/s; buffer concurrency fix holds |
| `packs/dms/streams/buffer.py` | Producers hold `_BUF_LOCK` briefly; DuckLake write under `_WRITE_LOCK` with **per-batch connection** (naive design was ~25 ev/s) |
| `bench/accuracy.py` | Separate correctness gate (`wrong==0` on core); not a load tool — soak must stub LLM so accuracy path stays cheap |
| B0 shipped | Ledger ~200 appends/s chain-valid; NL→SQL p95 ~56ms @ 6 threads (BUILD_PLAN_V2 header) |

**API surfaces for k6 (already live):**

- `POST /dms/query` — body `{question, session_id?}` → `answer_question` (`CortexOS/api/dms_query.py`)
- `POST /dms/streams/{stream_id}/events` — steward+ API key; max 1000 events/req; 429 on backpressure (`CortexOS/api/stream_routes.py`)
- Rate limit default: `DMS_RATE_LIMIT_PER_MIN=120` on `/dms/*` — **stress runs must raise or disable** (`DMS_RATE_LIMIT_PER_MIN=0` or high value) or k6 will measure the limiter, not DuckDB/streams

---

## 1. Scenario matrix

Pass bars are **CI/demo gates** for B1 acceptance. Nightly soak may use softer “warn” thresholds recorded in the artifact but not fail the PR job.

| Name | Tool | What it proves | Pass bar | Output artifact |
|---|---|---|---|---|
| `ledger_append_storm` | `python -m bench.stress --scenario ledger` | Hash-chain integrity under concurrent appends | `errors==0`, `chain_valid==true`, throughput ≥ **150** appends/s @ 8×25 | `bench/results/stress_last_run.json` (scenario object) + row in `lake.gold.benchmarks` |
| `nl_sql_query_concurrency` | `python -m bench.stress --scenario query` | In-process DuckDB NL→SQL under thread pool | `errors==0`, p95 ≤ **150 ms** @ 8 threads (B0 was ~56ms@6; allow headroom), throughput recorded | same JSON + gold |
| `stream_ingest_throughput` | `python -m bench.stress --scenario stream` | Buffer→bronze under contention | `errors==0`, events/s ≥ **300** @ 8 threads (baseline ~379) | same JSON + gold |
| `ingest_simulator_bronze` | `python -m scripts.stream_simulate` (± `--url`) | Sustained rows/s into bronze (S0 contract) | ≥ **50 ev/s for 60s**; all events in bronze within **5s** of send end (S0 ACCEPTANCE) | `bench/results/ingest_sim_last_run.json` |
| `k6_dms_query` | `bench/k6/dms_query.js` + Windows `k6.exe` | HTTP answer path under VUs | http_req_failed &lt; **1%**; p95 &lt; **500 ms** @ 10 VUs / 2m (stubbed LLM); no 5xx | `bench/results/k6_query_summary.json` (+ optional `.html`) |
| `k6_dms_streams` | `bench/k6/dms_streams.js` | HTTP stream ingest under VUs | failed &lt; **1%** (429 counted separately as backpressure); sustained ≥ **200** accepted events/s @ 10 VUs; flush depth recoverable | `bench/results/k6_streams_summary.json` |
| `chaos_api_kill` | PowerShell + pytest helper | API process kill mid-load → restart clean | Ledger `verify.ok`; no orphan partial bronze table; stream loses ≤ **1 unflushed batch** (S0 doc) | `bench/results/chaos_api_last_run.json` |
| `chaos_agent_kill` | PowerShell + DBOS resume (after S1 remainder) | `taskkill /F` mid agent workflow | Ledger chain valid; workflow **resumes**; **no duplicate publish**; no partial lake commit | `bench/results/chaos_agent_last_run.json`; unskips `test_workflow_resume_after_kill` |
| `soak_24h` | `bench/soak_profile.yaml` + runner | Stability under stubbed LLM + light ingest | 24h wall; crash count **0**; error rate &lt; **0.1%**; RSS growth &lt; **20%** vs hour-1 baseline; ledger still valid at end | `bench/results/soak_24h/<run_id>/metrics.jsonl` + summary JSON |
| `duckdb_concurrency_knee` | `python -m bench.stress --scenario knee` (new) | Degradation curve vs threads | Emits curve; documents **knee** = first thread count where p95 &gt; **2×** p95@1 **or** throughput drops &gt; **10%** vs previous step | `bench/results/duckdb_knee.json` (+ SVG/PNG optional later) |

**Unified envelope (recommended for Studio + gold):** every run also appends one row to `lake.gold.benchmarks` with columns sketched in §6.

---

## 2. k6 script outlines

**Binary:** Windows `k6.exe` checked into `bench/k6/` (BUILD_PLAN_V2 B1). Document SHA256 in `bench/k6/README.md`. Do not commit unrelated cloudflared-style binaries elsewhere.

**Shared env (both scripts):**

```text
BASE_URL=http://127.0.0.1:8787          # or demo API port
DMS_API_KEY=dms-demo-steward-key        # streams need steward+
DMS_RATE_LIMIT_PER_MIN=100000           # set on API process before load
```

### 2.1 `bench/k6/dms_query.js` — `POST /dms/query`

| Piece | Spec |
|---|---|
| Options | `stages: [{duration:'30s', target:5}, {duration:'2m', target:10}, {duration:'30s', target:0}]`; `thresholds: { http_req_failed:['rate<0.01'], http_req_duration:['p(95)<500'] }` |
| Setup | Health ping; optional warm-up single query |
| VU loop | Round-robin questions from the same mix as `QUERY_MIX` in `bench/stress.py` |
| Request | `POST ${BASE_URL}/dms/query` JSON `{question, session_id: 'k6'}` |
| Checks | status 200; body has `route` in `sql|rag|needs_clarification`; no uncaught 5xx |
| Stub | API must run with stubbed/disabled LLM so generative fallback cannot dominate latency |
| Tear-down | `--summary-export=bench/results/k6_query_summary.json` |
| Anti-scope | Do not assert answer correctness (that is `bench.accuracy`); load only |

### 2.2 `bench/k6/dms_streams.js` — `POST /dms/streams/{id}/events`

| Piece | Spec |
|---|---|
| Options | constant 10 VUs, 3m; batch size 20 events/req (match stress.py); thresholds on failed rate excluding tagged 429 |
| Setup | Ensure stream exists (`POST /dms/streams` or rely on auto-register) |
| VU loop | `POST /dms/streams/k6-stress/events` with unique `event_id`s (`${__VU}-${__ITER}-${j}`) |
| Headers | `X-API-Key: ${DMS_API_KEY}`, `Content-Type: application/json` |
| Checks | 200 → `accepted` == batch size; **tag** 429 as `backpressure` (informational metric, not automatic fail until depth stuck &gt; 30s) |
| Post-run | one `POST .../flush`; optional `GET .../preview` row_count sanity |
| Pass | sustained accepted events/s ≥ 200; backpressure clears after load stops |
| Tear-down | `--summary-export=bench/results/k6_streams_summary.json` |

**CLI examples (implementation slice, not run here):**

```text
bench\k6\k6.exe run --env BASE_URL=... --summary-export=bench/results/k6_query_summary.json bench/k6/dms_query.js
bench\k6\k6.exe run --env BASE_URL=... --summary-export=bench/results/k6_streams_summary.json bench/k6/dms_streams.js
```

---

## 3. Chaos-lite: taskkill API + agent

Windows-first (owner machine). Linux CI can emulate with `SIGKILL` later; primary assertions are identical.

### 3.1 API mid-run (`chaos_api_kill`)

**Procedure**

1. Start API under known PID (demo launcher or `uvicorn` wrapper); set high rate limit; temp `DMS_LAKEHOUSE_HOME`.
2. Start background load: either k6 streams **or** `stream_simulate --url` at 50 ev/s.
3. After ≥15s steady state: `taskkill /F /PID <api_pid>`.
4. Restart API against **same** lakehouse home + ledger DB.
5. Assert:
   - `ledger.verify().ok` (gap-free hash chain)
   - Bronze stream table readable; no half-created temp tables left visible in catalog
   - Event loss ≤ one unflushed buffer batch (S0 ACCEPTANCE) — compare `sent` vs `COUNT(*)` with documented slack
6. Resume light load 30s; `errors==0`

**Mid-run assertions (must appear in artifact):**

| Assert | Pass |
|---|---|
| `chain_valid` | true |
| `partial_lake_commit` | false (no orphan `_tmp_*` / failed swap tables) |
| `max_lost_events` | ≤ `DMS_STREAM_BATCH` (default 500; stress often uses 200) |
| `restart_serves_200` | true within 30s |

### 3.2 Agent worker mid-run (`chaos_agent_kill`)

**Depends on:** S1 DBOS durable resume (Cursor B1 / packet A1 verdict). Until then: keep `test_workflow_resume_after_kill` skipped; chaos scenario is **specced but blocked**.

**Procedure**

1. Start agent run that will spend ≥5s in a durable step (detect → gather → draft) with stubbed LLM.
2. `taskkill /F` the worker/API hosting DBOS mid-step.
3. Rerun / resume same `run_id`.
4. Assert:
   - Workflow resumes from last checkpoint (not restart-from-zero with duplicate side effects)
   - **No duplicate publish** (F8 artifact / report path unique per run)
   - Ledger chain valid; every step still auditable
   - No partial lake write from a half-finished publish tool

**Maps to:** BUILD_PLAN S1 ACCEPTANCE + B1 “taskkill /F API + agent worker”.

---

## 4. Soak 24h profile

**Goal:** Prove the demo stack does not leak, corrupt the ledger, or wedge DuckLake under continuous **light** load with **stubbed LLM** (no token burn, deterministic latency).

### 4.1 Load shape (steady)

| Lane | Rate | Notes |
|---|---|---|
| Query | 1 req / 5s | Rotate `QUERY_MIX`; expect `sql` route |
| Stream | 10 ev/s | `stream_simulate` or tiny k6 constant |
| Ledger | incidental | via stream flush + query audit |
| Agent | optional hourly | detector-only (0 LLM) if S1 present |
| Accuracy smoke | every 6h | `python -m bench.accuracy --tier core` — must stay `wrong==0` |

### 4.2 Controls

- Env: `DMS_LLM_STUB=1` (or existing stub flag — wire in B6 if missing)
- Rate limit raised for soak process only
- Dedicated data dir under `bench/results/soak_24h/<run_id>/`
- Heartbeat: write metrics every 60s to `metrics.jsonl` (`ts`, RSS, query_p95_1m, stream_depth, error_count, ledger_ok)

### 4.3 Pass / fail

| Metric | Pass |
|---|---|
| Duration | ≥ 24h wall clock (CI may run **1h soak-lite** with same profile; 24h is nightly/manual) |
| Process crashes | 0 |
| HTTP/in-process error rate | &lt; 0.1% |
| RSS | &lt; +20% vs hour-1 median |
| Ledger verify (end) | ok |
| Core accuracy (end) | wrong==0 |

### 4.4 Artifact

`bench/results/soak_24h/<run_id>/summary.json` + `metrics.jsonl` → also one `gold.benchmarks` row (`scenario=soak_24h`, `pass=bool`).

---

## 5. DuckDB concurrency knee method

**Purpose:** Find the practical concurrency ceiling for in-process NL→SQL (and optionally raw SQL) so Studio/docs can show an honest “serverless-by-architecture” capacity curve — not a single vanity p95.

### 5.1 Method

1. Fix iterations per thread (e.g. 20) and question mix (`QUERY_MIX`).
2. Sweep threads: `1, 2, 4, 8, 12, 16, 24, 32` (stop early if errors explode).
3. For each N, run `stress_queries(N, iterations)` (reuse `_run_pool`); record `throughput_per_s`, `p50_ms`, `p95_ms`, `max_ms`, `errors`.
4. Optional second curve: concurrent `buffer.append_events` only (stream knee) — separate series `stream_*`.
5. Define **knee** = smallest N where **either**:
   - `p95(N) > 2 * p95(1)`, or
   - `throughput(N) < 0.9 * throughput(N_prev)` (first drop &gt;10% vs previous step)
6. Report recommended operating point = largest N **before** knee with `errors==0`.

### 5.2 Output schema (`duckdb_knee.json`)

```json
{
  "generated_at": "...",
  "scenario": "duckdb_concurrency_knee",
  "points": [{"threads": 1, "p95_ms": 0, "throughput_per_s": 0, "errors": 0}],
  "knee_threads": 16,
  "recommended_threads": 12,
  "baseline_note": "B0 p95~56ms@6; stream~379ev/s@8"
}
```

### 5.3 Pass bar

Not a hard CI fail on knee location (hardware-dependent). CI checks: curve length ≥ 5 points; `errors==0` for all N ≤ 8; artifact written. Document knee in CHANGELOG when it moves after buffer/query changes.

---

## 6. How Studio BENCHMARKS tab will consume results

**U0 plan (BUILD_PLAN_V2 FEATURE U0):** tab renders `accuracy_last_run.json` + `stress_last_run.json` (precision, coverage, wrong=0 badge, p95) + steward “run benchmark” button. Thin API: `GET /dms/bench/latest`.

### 6.1 Phase A (U0 — file-backed, ship first)

| UI element | Source field |
|---|---|
| Wrong=0 badge | `accuracy_last_run.json` → tiers.*.wrong |
| Precision / coverage | tiers.core.answered_precision / coverage |
| Stress cards | each `scenarios[]` in `stress_last_run.json` — show name, throughput or events_per_s, p95_ms, errors, chain_valid |
| Run button | steward POST triggers `python -m bench.stress` + `bench.accuracy` subprocess; poll until JSON mtime updates |
| Poll | 5s (U0 anti-scope: no websockets) |

### 6.2 Phase B (B1 complete — gold table)

| UI element | Source |
|---|---|
| History sparkline | `SELECT * FROM lake.gold.benchmarks ORDER BY run_at DESC LIMIT 50` |
| Scenario filter | `scenario` column |
| Pass/fail chips | `pass` bool + `pass_bar` text |
| k6 / soak / chaos / knee | separate cards fed by latest row per scenario |

**Suggested `lake.gold.benchmarks` columns:**

`run_id`, `run_at`, `scenario`, `tool`, `threads`, `throughput`, `p50_ms`, `p95_ms`, `errors`, `pass`, `artifact_path`, `git_sha`, `host`, `notes_json`

**API:** `GET /dms/bench/latest` returns `{accuracy, stress, gold: [...latest per scenario]}` so the tab never scrapes the filesystem from the browser.

---

## 7. Links to truth-ground files

| Path | Role |
|---|---|
| `docs/dms/BUILD_PLAN_V2_LAKEHOUSE.md` § B1, § U0 BENCHMARKS, § S0/S1 ACCEPTANCE | Binding product spec |
| `docs/dms/CURSOR_EXEC_PACKET_2026-07-22.md` § A3 / B6 | Research vs code sequencing |
| `docs/research/findings/LAKEHOUSE_2026.md` | DuckLake / bronze commit semantics |
| `docs/research/findings/NL2SQL_ACCURACY_2026.md` | Accuracy philosophy (wrong=0); soak must not trade correctness for load |
| `docs/research/findings/STREAMING_ORCH_2026.md` | V0 webhook tier + DBOS chaos expectations |
| `bench/stress.py` | v0 harness to extend |
| `bench/accuracy.py` + `bench/golden/dms_golden_v1.yaml` | Correctness gate |
| `bench/results/stress_last_run.json` | Current stream baseline (~379 ev/s) |
| `bench/results/accuracy_last_run.json` | Studio Phase A input |
| `packs/dms/streams/buffer.py` | Concurrency model (`_BUF_LOCK` / `_WRITE_LOCK`) |
| `scripts/stream_simulate.py` | Ingest throughput / S0 simulator |
| `CortexOS/api/dms_query.py` | `POST /dms/query` |
| `CortexOS/api/stream_routes.py` | `POST /dms/streams/.../events` |
| `packs/dms/security/rate_limit.py` | Must be raised for load tests |
| `tests/dms/test_s1_agents.py` | `test_workflow_resume_after_kill` placeholder |
| `CHANGELOG_DMS.md` / `STATUS.md` | Shipped baselines |

---

## 8. Ordered Cursor coding slices (B6)

Do **not** implement in the A3 research session. Suggested merge order:

| # | Slice | Deliverable | Depends |
|---|---|---|---|
| 1 | **Harness envelope** | Extend `bench/stress.py` CLI (`knee`, unified multi-scenario write); stable JSON schema version field | — |
| 2 | **Knee finder** | `--scenario knee` → `duckdb_knee.json` | 1 |
| 3 | **Ingest sim artifact** | `stream_simulate` writes `ingest_sim_last_run.json`; optional assert vs S0 50/60/5 | — |
| 4 | **k6 vendor + scripts** | `bench/k6/k6.exe` + `dms_query.js` + `dms_streams.js` + README (rate-limit note) | API up |
| 5 | **Chaos API** | PS1 or pytest: taskkill API mid-stream; assertions → `chaos_api_last_run.json` | 4 or sim |
| 6 | **Chaos agent** | Unskip/wire `test_workflow_resume_after_kill`; `chaos_agent_last_run.json` | S1 DBOS resume (packet B1) |
| 7 | **Soak runner** | `bench/soak_profile.yaml` + 1h CI job + 24h manual script; stub LLM | 1 |
| 8 | **Gold writer** | Append each scenario to `lake.gold.benchmarks` | L0 lakehouse |
| 9 | **Studio wire** | `GET /dms/bench/latest` + BENCHMARKS tab cards for stress/k6/knee (U0 can ship Phase A earlier on files alone) | U0 shell |
| 10 | **CHANGELOG + STATUS** | Record pass bars hit; update stream/query baselines | after green runs |

**Anti-scope for B6:** no NATS/Redpanda load gen (S2); no Temporal; no claiming “Databricks-parity” throughput; no accuracy thresholds inside k6.

---

## 9. Acceptance checklist (B1 done when…)

- [ ] Scenario matrix artifacts exist for ledger, query, stream, k6×2, knee (chaos agent may wait on DBOS)
- [ ] Pass bars in §1 met on demo hardware (or documented waiver with numbers)
- [ ] Rate-limit bypass documented for load runs
- [ ] `lake.gold.benchmarks` receiving rows **or** Studio Phase A reading JSON (minimum)
- [ ] CHANGELOG_DMS entry for B1 suite
- [ ] This findings file remains the design source of truth until amended
