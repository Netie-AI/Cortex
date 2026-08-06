```yaml
keywords: [duckdb, serving_engine, postgres, cassandra, scylla, mssql, dynamodb, mcp, iceberg, five-ports, bronze-writer, warehouse-swap, connectors]
main_idea: "Keep DuckDB as serving warehouse; enterprise DBs enter via bronze connectors (or later serving_engine swap) — never Postgres-as-OLAP, Cassandra, or MCP ontology-in-customer-DB."
models: [grok-4.5]
workflow: none
reuse: golden_rule
status: verified
cite: task: dms-serving-warehouse-architecture-verdict
repo: DMS
date: 2026-08-05
```

# DMS serving warehouse — DuckDB vs Postgres / Cassandra / MSSQL / DynamoDB MCP

## Main idea

- DuckDB is the Tier-4 single-writer SQL serving path; Postgres is control plane only.
- Accuracy/throughput pain is Cortex routing + value normalization + schema retrieval — not DuckDB engine choice.
- Enterprise MSSQL/Dynamo/etc. sync into bronze under the same writer contract; MCP must not become a warehouse or run inference inside the customer DB.

## Keywords (search)

`duckdb`, `serving_engine`, `postgres`, `cassandra`, `scylla`, `mssql`, `dynamodb`, `mcp`, `iceberg`, `five-ports`, `bronze-writer`, `connectors`, `YAGNI`

## Questions left open

- When T9 external tables land: read-through vs materialize-to-bronze default for large MSSQL facts.
- Measured writer contention threshold that unlocks Iceberg (P-DMS-17) vs stay DuckDB+Parquet.
- Whether a future `ServingEnginePort` impl for StarRocks/Trino is customer-mandated or Netie-operated.

## Full answer / evidence

### 1. What DuckDB is doing today

- **Tier-4 serving warehouse**: `dms_serving.duckdb` / demo `dms_demo.duckdb` — single writer, read-only pools; refreshed from silver/gold (`DMS_TECHNICAL_ARCHITECTURE.md` §3 Tier 4; `demo_warehouse.py`, `ServingEnginePort.execute` in `packages/executor`).
- **Medallion + provenance**: bronze ingest attaches `_src STRUCT[]` + `_ingest_id`; promote contract-gates bronze→silver/gold with quarantine (`bronze.py`, `promote.py`, `lake_schema.py`). DuckDB chosen partly because struct arrays + A1 drill-through are native.
- **Only place `duckdb.execute` may live** (`CLAUDE.md` / ports): swap scenario for warehouse is already declared as `serving_engine` — DuckDB → other SQL/lake execute — not a sixth port.

### 2. Where accuracy / throughput bottlenecks actually come from

| Symptom people blame on "warehouse" | Real source |
|---|---|
| Wrong / plausible-but-wrong numbers | Value normalization (`BETA` vs `SKU-BETA`); Cortex routing / L0–L2; freeform SQL without certify (CLAUDE.md 10–12, P-DMS-19) |
| Missed paraphrase / schema miss | Schema retrieval + FreeRoute quality (P-DMS-21), not DuckDB scan speed |
| Latency on ask | Cortex HTTP path + model + EXPLAIN gate; SSE is answer stream not Kafka |
| Concurrent write pain | Intentional single-writer; Iceberg only when measured (P-DMS-17) |
| Control-plane load | Postgres `cortex`+`dms` — sessions, runs, catalog, doc index — not OLAP facts |

**Verdict:** Replacing DuckDB with Postgres/Cassandra/MSSQL does not fix the accuracy ceiling. Swapping the engine to chase "enterprise DB credibility" is cargo-cult.

### 3. Verdict table

| Option | Fit as serving OLAP | Accuracy impact | Enterprise sales fit | Law conflict |
|---|---|---|---|---|
| **Postgres (serving)** | Poor — OLTP control plane already; not columnar analytics; conflates planes | None / worse (lose STRUCT[] ergonomics, mix RLS control with fact scans) | "We use Postgres" story is false comfort | **Y** if used as second warehouse meaning or sixth store; control Postgres must stay non-serving |
| **Cassandra / Scylla** | **No** — wide-column KV, not analytics SQL engine; no governed metric SQL story | Negative — forces non-SQL answer path or reinvent SQL on top | "Scale" buzzword; wrong buyer problem | **Y** — new store outside five ports; breaks duckdb/serving_engine contract |
| **MSSQL plugin (as serving engine)** | Only if implement full `ServingEnginePort` + provenance + single-writer semantics | Neutral if SQL dialect + types preserved; high risk of silent cast bugs (already seen in promote) | Strong for "don't move our ERP" | **N** if behind `serving_engine` swap; **Y** if parallel path bypassing bronze/manifest |
| **DynamoDB via MCP "ontology+inference inside"** | **No** — MCP is tool I/O, not warehouse; inference belongs in Cortex HTTP | **Negative / P0 risk** — invents answers outside F5/manifest/ledger | Sounds modern; breaks 0-confidently-wrong | **Y** — ontology/answers via Cortex only; never infer inside customer Dynamo |
| **Keep DuckDB + connectors → bronze** | **Yes** — current law and product thesis | Positive — provenance, quarantine, certify stay intact | Honest: "sit on top of your DB by syncing facts" | **N** |
| **Iceberg later** | Yes when concurrent writers / multi-engine readers | Neutral–positive at scale | Lakehouse mandate customers | **N** — parked P-DMS-17; same product-on-top thesis |

### 4. Recommended path (big company stream/migrate) without breaking five-port law

1. **Ingest, don't relocate the product.** Customer MSSQL / Postgres / Dynamo / Salesforce → connector sync → **same bronze writer** (`_src`, receipt, quarantine) → silver → DuckDB serving. Architecture already shows `postgres://erp-prod` as a synced source, not the answer engine.
2. **Streaming = last.** Kafka/NATS only behind the same bronze contract (`§13` stage last; SPACES §6). Never a parallel ingest that skips triage.
3. **External tables (T9)** = optional read-through for huge cold facts; still scoped by signed manifest; prefer materialize hot analytical facts into silver for provenance + speed.
4. **True engine swap** only when measured: implement another `ServingEnginePort` (StarRocks/Trino/Iceberg-backed) — same five ports, same Cortex HTTP ontology, same F5. Do not add a sixth abstraction.
5. **Do not** turn control-plane Postgres into the serving warehouse. Do not add Cassandra. Do not run inference in the customer's DB.

### 5. What MCP should and must NOT do

| MCP may | MCP must NOT |
|---|---|
| Help operators discover schemas, credentials, sync jobs, health | Be the serving warehouse or SQL execute path |
| Orchestrate connector sync into bronze (tooling around worker runs) | Host ontology, routing, or "inference" over DynamoDB/MSSQL rows |
| Surface audit/run status for humans/agents | Bypass manifest, F5, or Cortex `/v1` answer path |
| | Invent a sixth port ("mcp_store") |

**One line:** MCP is a remote-control hand for ingest/ops; Cortex+serving_engine remain the brain and the calculator.

### 6. Founder answer (one paragraph)

Do not replace DuckDB with Postgres, Cassandra, or "MCP into DynamoDB." Postgres is already the control plane; Cassandra is not an analytics engine; MCP is not a warehouse and must never run ontology or inference inside the customer's database — answers stay Cortex HTTP. For big companies that want their MSSQL/Dynamo to participate: sync (or later stream) into bronze under the existing single-writer contract, serve from DuckDB (Iceberg only when concurrent writers force it), and if a customer mandates another SQL engine, swap only behind the existing `serving_engine` port. That keeps five ports, provenance, and 0 confidently wrong. Anything else is a second product.

## Golden rule (if reusable)

> Customer DBs are **sources** (connector → bronze → silver → DuckDB/`serving_engine`). Never make Postgres, Cassandra, MCP, or the customer's live OLTP the answer warehouse; accuracy lives in Cortex routing + value normalization + abstain, not in engine brand names.

## Verify

```bash
# Law surfaces (read-only)
rg -n "serving_engine|duckdb.execute|Five ports" D:/DMS/packages/core/dms_core/ports.py D:/DMS/CLAUDE.md
rg -n "Tier 4|Iceberg|Streaming brokers|Deliberately not built" D:/DMS/DMS_TECHNICAL_ARCHITECTURE.md
rg -n "P-DMS-17|on top of the warehouse|bronze writer" D:/DMS/PARKING_LOT.md D:/DMS/docs/SPACES.md
```

## Promote?

- [x] `docs/subagents_findings` only
- [ ] `~/.claude/skills|workflows|findings`
- [ ] `~/.cursor/skills|workflows|subagents|findings`
- [ ] `skill_distill/captures` + ingest
