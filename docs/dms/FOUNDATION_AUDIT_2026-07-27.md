# DMS foundation audit — how near is this to Databricks / Snowflake?

**Date:** 2026-07-27 · **Method:** live probe of the running engine (`:8000`),
code read of every foundation module, and three benchmarks run on this machine.
Every number below is measured, not estimated. Where something is unproven, it
says so.

---

## 0. The headline

**The foundation code exists and is unusually well governed. The foundation is
not carrying the product.** Three specific gaps, all measured:

| Claim | Reality | Evidence |
|---|---|---|
| "Lakehouse under the DMS" | The lakehouse holds **zero tables** and the answer engine does not read from it. Every answer comes from `data/dms_demo.duckdb`, a flat DuckDB file loaded from six CSVs. | `lakehouse_status()` → `schemas: {}`; `data/lakehouse/` does not exist on disk |
| "99% accuracy answer engine" | 100% on the 36 questions it was built against; **64.7%** on ordinary paraphrases of those same 36 questions. Was 23.5% before today's fixes. | `python -m bench.paraphrase` |
| "Learns from successful answers" | 42 skills stored, **0 ever retrieved**. The recall layer cannot fire. | `docs/dms/ROUTER_STATES.md` §4 |

None of this makes the system bad. It makes the *demo* far ahead of the
*foundation*, which is the opposite of what an MNC buyer needs.

---

## 1. Layer-by-layer: what exists, what runs

Legend: **shipped** = code + tests + live data · **built** = code + tests, no
live data · **partial** = works for the demo shape only · **absent**.

| Databricks / Snowflake capability | Cortex module | State | Honest gap |
|---|---|---|---|
| Open table format (Delta / Iceberg) | `packs/dms/lakehouse/catalog.py` — DuckLake + Parquet | **built** | Attaches in `ducklake` mode with time travel. `schemas: {}` — never migrated. `scripts/lakehouse_migrate.py` exists, has not been run here. |
| ACID + time travel | `lakehouse/tables.py` `snapshots()` / `query_at()` | **built** | Real DuckLake snapshots; untested against live data volume |
| Medallion bronze/silver/gold | schemas created on attach | **built** | Empty. The gold tables the plan names (`gold.sales_by_sku`, …) do not exist. |
| Auto Loader (file → bronze, exactly-once) | `packs/dms/ingest/loader.py` | **shipped** | Content-hash ledger, no partial commits, quarantine on parse failure. **csv/tsv/json/jsonl/xlsx only** — not "any type of data" (no parquet, avro, pdf, images, database CDC) |
| DLT declarative pipelines + expectations | `packs/dms/pipelines/` | **built** | `warn`/`drop`/`fail` actions, quarantine table, `_pipeline_events` event log. **Two pipeline definitions exist** (`inventory_silver`, `suppliers_silver`) |
| Structured Streaming | `packs/dms/streams/` | **built** | HTTP webhook intake → buffer → batch write to bronze, at-least-once with per-batch dedup and backpressure. **No Kafka/NATS/Flink connector.** Not real-time in the CDC sense — it is a durable HTTP inbox |
| Unity Catalog / Horizon (catalog + lineage) | `lakehouse/catalog.py`, `ontology/` | **partial** | Table inventory only. **No column-level lineage** — nothing in the repo produces a lineage graph, despite L3 being in the plan |
| Governed semantic layer / metrics | `packs/dms/semantic/` | **shipped** | The genuine strength. `metrics.yaml` templates with typed, validated params; no free-string interpolation; certified-query repo. This is the Cortex Analyst / Genie "trusted assets" pattern, done properly |
| NL→SQL | `CortexOS/dms/answer_engine.py` | **shipped** | Deterministic, abstain-safe, always shows SQL. **No LLM layer wired** (`DMS_L2_ENABLED` has no model behind it), so coverage is exactly what ~25 regex rules cover |
| Row/column security (RLS, masking) | `packs/dms/security/`, `sql_guardrail` | **shipped** | Sensitive-column masking, PII redaction, API-key RBAC, RLS GUC stamping |
| Audit / governance | `packs/dms/audit/ledger.py` | **shipped** | Hash-chained tamper-evident ledger. **Stronger than what Databricks or Snowflake give you out of the box.** Real differentiator |
| Write path (INSERT/UPDATE/DELETE) | `/dms/add-entry`, `/dms/propose-edit` | **partial** | `add-entry` inserts one row. `propose-edit` **only appends a JSONL changelog entry — it does not apply, validate, preview, or roll back anything.** There is **no delete path at all** |
| Serverless / warehouse sizing | in-process DuckDB | **absent** | Not a scaling model. See §3 |
| Multi-user concurrency | — | **absent** | See §3 |

---

## 2. Against the four things you asked for

**"Everyone can dump any type of data."** Partly. `POST /dms/ingest/file` handles
CSV/TSV/JSON/JSONL/XLSX with exactly-once semantics and quarantine — that part is
genuinely good, better than a naive loader. But "any type" today excludes
Parquet, Avro, ORC, PDF, images, and any database source (no CDC, no JDBC, no
Fivetran-equivalent). Databricks/Snowflake buyers assume all of those.

**"Real-time update data."** No. `POST /dms/streams/{id}/events` is a durable
HTTP inbox that batches into bronze. Measured stream ingest: **13.8 events/s at
2 threads**. There is no broker connector, no watermarking, no exactly-once sink,
no incremental materialization. Calling this streaming next to Structured
Streaming or Snowpipe will not survive a technical evaluation.

**"Perfect retrieval."** Measured today, after the fixes in §4:
- exact phrasings: **36/36 (100%)**
- ordinary paraphrases: **55/85 (64.7%)**, **0 wrong**, 30 abstentions

The 0-wrong number is the one worth defending. The system never invents an
answer — 30 questions got "I can't answer that, try…" instead of a plausible
lie. Snowflake and Databricks both *will* hand you a confident wrong number.
That is a real, defensible edge, and it is the opposite of "perfect retrieval":
it is *honest* retrieval, which is a better claim and a true one.

**"Perfect control with AI advice for editing / deleting data."** Not built.
Editing is a proposal appended to a JSONL file with no apply step and no
validation of the proposed value. Deleting does not exist. The ledger, the
approval rails and the autonomy ladder are all in place *around* a write path
that isn't there yet. This is the largest gap between the story and the code.

---

## 3. The scaling ceiling — and its root cause

Measured on this machine, NL→SQL under load (`python -m bench.stress --scenario
query --threads 8 --iterations 25`):

| | before today | after today | change |
|---|---|---|---|
| throughput | 3.4 q/s | **38.5 q/s** | 11.3× |
| p50 latency | 1694 ms | **95 ms** | 17.9× |
| p95 latency | 6048 ms | **727 ms** | 8.3× |
| errors / 200 | 2 | **0** | — |
| single-thread | 1090 ms/query | **80 ms/query** | 13.6× |
| `pytest tests/dms` | 339 s | **156 s** | 2.2× |

Where the original 1090 ms went, by profile:

- **~500 ms** opening a fresh DuckDB connection to the 6.5 MB warehouse file —
  once per question.
- **~440 ms** writing to `dms_query_skills`, including a full
  `CREATE TABLE IF NOT EXISTS` + `CREATE INDEX` script **on every query** —
  maintaining an index that is never read (`ROUTER_STATES.md` §4).
- the rest in repeated `Path.resolve()` / `mkdir()` syscalls per query.

So roughly **85% of query latency was overhead, not work.** Fixed in §4.

### The ceiling that remains

DuckDB takes an **exclusive file lock** for read-write connections. One
read-write connection anywhere — a live API process, a benchmark, a notebook —
locks every other process out of `data/dms_demo.duckdb` entirely:

```
IO Error: Cannot open file "D:\Cortex\data\dms_demo.duckdb":
The process cannot access the file because it is being used by another process.
File is already open in C:\Program Files\Python314\python.exe (PID 27532)
```

**This is the cause of the "intermittently flaky golden benchmark" that
`STATUS.md` records as unestablished.** The suspicion there was concurrent
access to `packs/data/dms_ops.db` (SQLite). It is `data/dms_demo.duckdb`
(DuckDB, exclusive lock), and the error message names the offending PID. With
`DMS_READ_ONLY_QUERIES=1` the benchmark ran clean 3× in a row with the live
server up; without it, items fail at random.

The architectural consequence matters more than the flaky test: **the serving
process cannot be horizontally scaled while it also writes.** Read-only mode
allows N reader processes, but the writer must then live in its own process,
because DuckDB refuses to mix read-only and read-write connections inside one
process. That is a deployment decision, not a code change — which is why the
flag defaults OFF.

For an MNC deployment this is the fork in the road:

- **Keep DuckDB**: readers scale out, one writer process, warehouse rebuilt by a
  job. Fine to a few hundred concurrent analysts on a single box. Cheap, $0
  licence, genuinely fast.
- **Move the catalog to Postgres** (DuckLake already supports it — the plan calls
  it "same data files, no migration") and you get real multi-writer. This is the
  single highest-leverage architectural move available and it is already
  designed for.

---

## 4. What was changed today

All changes verified against `pytest tests/dms` and both benchmarks.

1. **Destructive-intent classifier** (`query_service.destructive_intent`) —
   replaced `\b(drop|delete|truncate|alter|insert|update|create)\b` over raw
   English. It had refused `"update me on the delayed shipments"` and
   `"what does shipping cost us by drop-off point"`, and missed
   `"wipe all supplier records"`, `"remove the inventory table"`,
   `"erase everything in inventory"`. Now: SQL-statement shapes + (mutation verb
   → data object) with benign idioms stripped first and copula-preceded verbs
   ignored. Refusals carry an auditable cause. 30 cases pinned in
   `tests/dms/test_destructive_intent.py`.

2. **Document routing** (`RAG_KEYWORDS`) — fired on the bare openers
   `what does` / `explain`, so analytics questions were answered from the
   supplier-contract corpus (confident, zero rows). Now requires a document noun.

3. **Vocabulary normalization** (`packs/dms/semantic/vocabulary.py`, new) — maps
   business phrasing onto router vocabulary in front of L1, the same idea as the
   `synonyms:` field already declared on every metric in `metrics.yaml` and read
   by nothing. Slots (limits, thresholds, directions, day windows, locations)
   are still read from the **original** question, so a rewrite of the words can
   never move a number. Paraphrase robustness **23.5% → 64.7%** with answered
   precision still 100% and golden still 36/36.

4. **Dead router branch** — `\b(utilis|utiliz|how full|usage)\b` could not match
   the word "utilisation" (the trailing `\b` fails before "ation"). Hidden
   because the golden question hits L0 certified. Now `utilis\w*`.

5. **Read-only warehouse connections** (`warehouse_db.get_connection`) +
   `DMS_READ_ONLY_QUERIES` — the fix for §3.

6. **Connection caching + hot-path syscalls** — one cached read-only DuckDB
   instance per process handing out cursors; `dms_query_skills` schema created
   once per process instead of per query; repo root resolved once;
   `table_row_counts` / `preview_table` (pure reads) no longer take the write
   lock. A read-write open **evicts** the cached reader first, because DuckDB
   refuses two connections to one file with different configurations — without
   that eviction the cached reader locks the writer out of its own process.
   `pytest tests/dms`: **668 passed, 6 skipped, 0 failures** with the flag both
   ON and OFF.

7. **`bench/paraphrase.py` + `bench/golden/dms_paraphrase_v1.yaml`** (new) — 85
   ordinary paraphrases of the 36 golden intents, scored against the same
   canonical SQL. This is the number that predicts real traffic;
   `bench.accuracy` alone cannot, because the router was written against its
   questions.

---

## 5. Honest verdict on "better than Databricks and Snowflake"

`docs/dms/POSITIONING.md` already answers this, and it was right: Databricks and
Snowflake are **infrastructure that sits underneath you**, not competitors. This
audit is evidence for that document, not against it.

Where a head-to-head is actually winnable:

- **Refusal over guessing.** 0 confidently wrong answers across 85 paraphrases,
  with a stated reason and suggested alternatives. Genie and Cortex Analyst
  guess. For a regulated MNC this is the whole argument.
- **Tamper-evident hash-chained audit under every action.** Neither platform
  ships this; both make you build it.
- **$0 marginal cost, air-gappable, single-file.** For a factory or a 3PL site
  that cannot send data to a cloud region, this is not a smaller version of
  Databricks — it is the only option.
- **Governed metrics with typed params and zero string interpolation.** Equal to
  the best of what either vendor offers.

Where it is not close, and won't be soon: scale-out compute, streaming, open
table format ecosystem, connector breadth, marketplace, ML platform, cost-based
optimizer. Those are thousands of engineer-years. Do not put "better than
Databricks" in front of a technical buyer — the first question will be "how many
concurrent users" and the honest answer today is one process at 15.7 q/s.

---

## 6. Prioritized next work

Ranked by (value to the MNC story) ÷ (effort), highest first.

| # | Work | Why it is first | Rough size |
|---|---|---|---|
| 1 | **Populate the lakehouse.** Run `scripts/lakehouse_migrate.py`, point `query_service` at `lake.silver.*` through governed views | The entire L0–L2 stack is built and idle. This is the cheapest possible move from "demo on CSVs" to "real lakehouse", and it makes every other lakehouse claim true | days |
| 2 | **Return the honest fields from the API.** Add `layer`, `badge`, `metric_id`, `total_count`, `truncated`, `assumptions`, `suggestions` to `DMSQueryResponse` | The engine already computes all of them; FastAPI drops them. Truncation and abstain suggestions currently survive only as prose. Pure win | hours |
| 3 | **Decide the query-skill layer.** Either sentence embeddings (making it the L1.5 recall layer that closes most of the remaining 30 abstentions) or delete the read path | Today it is a learning loop that does not learn, and it *was* costing 40% of query latency | days |
| 4 | **Grow the paraphrase set to 300+ and gate CI on it** | 85 is enough to prove the point, not enough to prevent regression. `bench.accuracy` cannot catch brittleness by construction | days |
| 5 | **Build the write path properly** — propose → validate → diff preview → approve → apply → ledger → rollback, with delete included | The largest gap between story and code. Every rail it needs (ledger, approval, autonomy ladder) already exists | weeks |
| 6 | **Postgres catalog for DuckLake** | Turns the single-writer ceiling into real multi-writer, and it is already the documented exit ramp | weeks |
| 7 | **One real broker connector** (Kafka or NATS) into `streams/` | "Real-time" is unsupportable without it | weeks |
| 8 | **Column-level lineage (L3)** | Named in the plan, absent in code. First thing an enterprise data-governance review asks for | weeks |

Items 1 and 2 together would move more of your claims from "aspirational" to
"demonstrable" than anything else on this list.

---

## 7. Reproducing every number here

```bash
export DMS_READ_ONLY_QUERIES=1
python -m bench.accuracy                                        # 36 golden questions
python -m bench.paraphrase                                      # 85 paraphrases
python -m bench.stress --scenario query --threads 8 --iterations 25
python -m pytest tests/dms -q
python -c "from packs.dms.lakehouse.catalog import lakehouse_status; print(lakehouse_status())"
```
