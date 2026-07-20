# Lakehouse Research for Cortex DMS — July 2026

Research date: 2026-07-20. Scope: table format choice (DuckLake vs Delta vs Iceberg), Databricks concepts to mirror, governance/lineage for a local-first, $0-infra, DuckDB-centric DMS on Windows 11.
Legend: **[V]** = verified against linked primary/current source. **[I]** = inference/synthesis, not directly verified.

---

## 1. DuckLake (duckdb/ducklake)

- **[V]** DuckLake **v1.0 released 2026-04-13**, alongside DuckDB v1.5.2. Explicitly "production-ready release with guaranteed backward-compatibility" of the spec. v1.1 spec expected **Sept 2026**. — https://ducklake.select/2026/04/13/ducklake-10/
- **[V]** Architecture: all lakehouse metadata (snapshots, schemas, stats, file lists) lives in **any ACID SQL database**; data lives in plain **Parquet**. No manifest/JSON/Avro metadata files, no REST catalog server required. — https://ducklake.select/faq
- **[V]** v1.0 features: ACID multi-table transactions, snapshots + **time travel**, **schema evolution**, **data inlining** (small writes stored in catalog DB, default on, 10-row threshold), **sorted tables**, **bucket partitioning** (murmur3, Iceberg-compatible), geometry type + bbox stats, variant type, **Iceberg v3-compatible deletion vectors** (Puffin files, marked *experimental*). — https://ducklake.select/2026/04/13/ducklake-10/
- **[V]** ~108 PRs merged for 1.0 since late 2025; 68 on reliability/correctness. — https://motherduck.com/blog/duckdb-ecosystem-newsletter-april-2026/
- **[V]** Catalog databases & concurrency: **DuckDB file** = single client; **SQLite** = multiple *local* processes (attach/detach per query + write-lock retry timeout); **PostgreSQL** = full multi-user, remote clients, transactional coordination; **MySQL 8+** works but **not recommended** (known issues). — https://ducklake.select/docs/stable/duckdb/usage/choosing_a_catalog_database
- **[V]** Encryption: `ENCRYPTED` flag at ATTACH; every Parquet file gets its own auto-generated key stored in the catalog (`ducklake_data_file.encryption_key`) → data files can sit on untrusted storage. — https://ducklake.select/docs/stable/duckdb/advanced_features/encryption
- **[V]** Maintenance & CDC built in: `ducklake_expire_snapshots()`, `ducklake_cleanup_old_files()`, `ducklake_merge_adjacent_files()` (compaction); change feeds via `ducklake_table_insertions()/deletions()/changes()`. — https://duckdb.org/docs/current/core_extensions/ducklake
- **[V]** Usage (SQL, identical from Python via `duckdb` pip package): `ATTACH 'ducklake:metadata.ducklake' AS lake (DATA_PATH 'data/')` — the `ducklake` core extension autoloads. SQLite/Postgres catalogs use `ducklake:sqlite:...` / `ducklake:postgres:...` URIs. — https://duckdb.org/docs/current/core_extensions/ducklake
- **[V]** Iceberg interop: "copy from DuckLake to Iceberg" is supported (and bucket partitioning + deletion vectors are deliberately Iceberg-compatible). — https://ducklake.select/faq
- **[V]** Windows: `windows_amd64` extension builds ship normally; **windows_arm64 builds missing** (403 on install, issue #390) — irrelevant for the RTX 4070 x64 target. — https://github.com/duckdb/ducklake/issues/390
- **[I]** No Windows-x64-specific defects surfaced in issue triage; open issues cluster around cloud paths (ADLS/OneLake) and ODBC, not local NTFS paths.
- Roadmap: **[V]** v1.1 = variant inlining, multi-DV Puffin; v2.0 (distant) = git-like branching, roles, incremental materialized views. — https://ducklake.select/2026/04/13/ducklake-10/
- Context: **[V]** current stable DuckDB is v1.5.4 (2026-06-17); v1.6.0 in dev. — https://github.com/duckdb/duckdb/releases

## 2. delta-rs / `deltalake` Python package

- **[V]** Current: **python-v1.6.2 (2026-07-08)**. Recent line: 1.6.1 (2026-06-24) added **column mapping write support** + generic OpenDAL backend; 1.6.0 (2026-05-19) PyArrow ≥21, variant preview, **Windows UNC path fix**; 1.5.0 (2026-03-12) parallel partition writers, disk-spilling merge, log compaction; 1.4.2 (2026-02-09) deletion-vector metadata exposure. — https://github.com/delta-io/delta-rs/releases
- **[V]** Deletion vectors: **read** of DV-enabled tables works (via delta-kernel-rs); **write to DV-enabled tables not supported** — feature request opened 2026-01-14, no shipped release notes confirm it landed as of 1.6.2. — https://github.com/delta-io/delta-rs/issues/4079
- **[I]** Liquid clustering: not available in delta-rs (Spark/Databricks-side feature); delta-rs offers `optimize` (bin-packing + z-order) only. V2-checkpoint reading comes via delta-kernel-rs; writer-side coverage is partial (log compaction since 1.5.0) — treat advanced writer features as engine-dependent.
- **[V]** Ongoing migration of delta-rs internals onto **delta-kernel-rs** to close protocol gaps (DVs, column mapping). — https://github.com/delta-io/delta-rs/discussions/2210
- **[V]** DuckDB interop: `duckdb-delta` extension (built on delta-kernel-rs) **left experimental status in May 2026** — reads (incl. DVs, partitions), **writes (blind INSERT/append only)**, **time travel**, Unity Catalog attach. — https://duckdb.org/2026/05/07/delta-uc-updates
- **[V]** Windows: first-class wheels; UNC path support explicitly fixed in 1.6.0. — https://github.com/delta-io/delta-rs/releases

## 3. PyIceberg + Apache Iceberg v3

- **[V]** Current **PyIceberg 0.11.1 (released 2026-03-03)**, Python ≥3.10; 0.11.0 (Feb 2026) was a large release (380+ PRs, 50+ contributors). — https://pypi.org/project/pyiceberg/ , https://iceberg.apache.org/blog/apache-iceberg-python-0.11.0-release/
- **[V]** Writes: append, overwrite, dynamic partition overwrite, **upsert**, delete, `add_files`; branches in `add_files`. **Format v3: read support only** — no v3 writes (no DV writes) yet in 0.11.x. — https://github.com/apache/iceberg-python/releases
- **[V]** Local catalog: `SqlCatalog` runs on **SQLite or Postgres** — pure-local Iceberg without a REST server is possible for PyIceberg, but…
- **[V]** DuckDB `iceberg` extension: full reads; **writes only through an Iceberg REST Catalog** (INSERT/UPDATE/DELETE/MERGE INTO, ALTER TABLE, partition transforms; v3 tables get compact Puffin deletion vectors, v2 get positional-delete Parquet; merge-on-read only, no copy-on-write). duckdb-iceberg v1.5.3 features blog 2026-05-29. — https://duckdb.org/docs/current/core_extensions/iceberg/writing , https://duckdb.org/2026/05/29/new-iceberg-features
- **[I]** Net for local-first: DuckDB cannot write Iceberg against PyIceberg's SQLite `SqlCatalog` — you'd have to run a REST catalog (Lakekeeper/Polaris) as a service. That breaks "$0-infra, zero services" unless accepted as an optional container.
- **[V]** Ecosystem: Iceberg **v3 is the industry convergence point** — Databricks Iceberg v3 public preview; Snowflake Iceberg v3 support preview 2026-03-04; Java Iceberg 1.10/1.11 (2025→May 2026) matured v3 (deletion vectors, variant, row lineage). — https://www.databricks.com/blog/next-era-open-lakehouse-apache-icebergtm-v3-public-preview-databricks , https://docs.snowflake.com/en/release-notes/2026/other/2026-03-04-iceberg-v3-support-preview

## 4. Format recommendation for Cortex

**Verdict: DuckLake as the lakehouse core. SQLite catalog by default; Postgres/Supabase catalog when multi-user. Iceberg (via export) is the exit ramp, not the core.**

- **[I]** Fit: Cortex is DuckDB-centric, small-data-now (25k rows → tens of GB). DuckLake is the only format where the *whole* lakehouse (ACID, time travel, schema evolution, encryption, compaction, CDC) works natively in-process with zero extra services on Windows. Data inlining specifically solves the small-write/many-small-files problem Delta and Iceberg both suffer at this scale.
- **[I]** Delta (delta-rs) is the runner-up: mature Python writer, good Windows story, DuckDB read+append. But: two engines (deltalake lib + DuckDB) coordinating one table, no DV writes from Python, and Databricks itself is pivoting neutral-interchange to Iceberg.
- **[I]** Iceberg-first is premature for this stack: PyIceberg has no v3 writes, and DuckDB Iceberg writes demand a REST catalog service. Revisit if/when DuckDB gains REST-free Iceberg writes or PyIceberg 1.0 lands v3.
- Migration/interop story (client later moves to Databricks/Snowflake):
  - **[V]** DuckLake → Iceberg copy is supported today; DuckLake's bucket partitioning and deletion vectors are Iceberg-compatible by design. — https://ducklake.select/faq
  - **[V]** DuckDB can write directly into a customer's Iceberg REST catalog (Unity Catalog exposes the Iceberg REST Catalog API; Snowflake Open Catalog is Polaris) — so "export = `COPY ... TO iceberg_catalog`" from the same engine. — https://duckdb.org/docs/current/core_extensions/iceberg/writing , https://www.databricks.com/blog/announcing-full-apache-iceberg-support-databricks
  - **[I]** Worst case, plain Parquet export always works — DuckLake data files are already standard Parquet, so "no lock-in" is a defensible claim.

## 5. Databricks concepts to mirror (honest local analogs)

- **Medallion (bronze/silver/gold)** — **[V]** Bronze = raw, minimal validation, permissive types (strings/VARIANT) to survive schema drift; Silver = deduped/validated, ≥1 non-aggregated validated representation of each record, fed incrementally from bronze; Gold = dimensional/aggregated, per-consumer, performance-optimized. Explicitly "recommended best practice, not a requirement." — https://docs.databricks.com/aws/en/lakehouse/medallion
- **Auto Loader** — **[V]** `cloudFiles` streaming source: incremental file discovery; discovered-file metadata persisted in RocksDB at the checkpoint location → **exactly-once by file path**, resumable after failure; schema inference/evolution with a `_rescuedData` escape hatch. Local analog: a file-ledger table (path+mtime/hash) in the ops DB driving incremental loads into bronze. — https://docs.databricks.com/aws/en/ingestion/cloud-object-storage/auto-loader/
- **Lakeflow Declarative Pipelines (ex-Delta Live Tables, renamed DAIS June 2025)** — **[V]** declarative tables/materialized views + **expectations**: per-record SQL boolean constraints on any table/view with three actions (warn/keep, **drop**, **fail**), quality metrics recorded in the pipeline event log; core donated to Apache Spark as "Spark Declarative Pipelines". — https://docs.databricks.com/aws/en/ldp/expectations , https://docs.databricks.com/aws/en/ldp
- **Unity Catalog governance** — **[V]** central grants on a 3-level namespace, automatic runtime **lineage** (table + column level), tags; **ABAC row-filter & column-mask policies, governed tags, and auto data classification hit GA 2026-05-13**. — https://www.databricks.com/blog/abac-row-filtering-and-column-masking-policies-governed-tags-and-data-classification-are-now
- **DBSQL serverless vs classic** — **[V]** serverless = Databricks-owned compute, 2–6 s startup, AI-driven Intelligent Workload Management autoscaling; classic/pro = customer-account clusters, ~4 min startup, threshold autoscaling. — https://docs.databricks.com/aws/en/compute/sql-warehouse/warehouse-types
  - **[I]** Local analog: in-process DuckDB *is* the "serverless" experience (zero warm-up, no cluster to size); "choose-your-cluster" maps to selectable engine profiles (threads/memory caps, optional GPU-accelerated steps). Frame it as "serverless-by-architecture."
- **AI/BI Genie grounding** — **[V]** Genie grounds NL→SQL in: Unity Catalog metadata (table/column comments), curated table scope per "space", **instructions** (business terminology), **example/certified SQL**, column synonyms + **sampled values**, certified metrics; answers generated from parameterized trusted queries are badged **"Trusted"**; benchmarks for accuracy evaluation. — https://docs.databricks.com/aws/en/genie/
- **2025–2026 shifts to know** — **[V]** DAIS June 2025: **Lakebase** (managed Postgres OLTP, Neon acquisition; GA early 2026) + **Managed Iceberg Tables** in UC (public preview) + Iceberg REST Catalog API. DAIS June 2026 (16–18): **Genie One** (agentic coworker, GA), **Genie Agents**, **Genie Ontology** (self-improving org context layer), Genie App Builder (preview). — https://www.databricks.com/events/dataaisummit-2025-announcements , https://www.databricks.com/blog/introducing-genie-one-genie-ontology-and-genie-agents
  - **[I]** Takeaway: Databricks' own direction (Postgres for OLTP + open format for analytics + catalog-grounded NL) validates Cortex's SQLite/Postgres-ops + DuckLake-analytics + metadata-grounded-NL split.

## 6. Open-source governance / catalog / lineage

- **Unity Catalog OSS** — **[V]** v0.5.1 (July 2026); JVM (JDK 17) server + UI; Delta/Iceberg-REST/UniForm-Hudi; LF AI & Data **sandbox** project; README: "APIs are currently evolving and should not be assumed to be stable." **[I]** Verdict: too heavy and too immature to embed in a local SME deployment. — https://github.com/unitycatalog/unitycatalog
- **Apache Polaris** — **[V]** Apache **Top-Level Project Feb 2026**; releases 1.0→1.6 (monthly cadence to June 2026); single binary or Helm + Postgres; catalog federation (Glue/HMS/other REST), generic tables (Delta/Hudi) GA, credential vending, OPA/Ranger authz. Still "one more JVM service on your critical path." (State-of-Polaris write-up, July 2026 — secondary source.) — https://dev.to/alexmercedcoder/the-state-of-apache-polaris-in-july-2026-from-incubating-catalog-to-the-governance-layer-of-the-1n3h , https://polaris.apache.org/
- **Lakekeeper** — **[V-secondary]** Rust single-binary Iceberg REST catalog; the lightweight choice *if* an Iceberg REST endpoint is ever needed locally. — https://estuary.dev/blog/iceberg-catalog-apache-polaris-vs-unity-catalog/
- **[I]** Verdict: for one laptop + a handful of users, all three are overkill. DuckLake's catalog *is* the catalog (queryable SQL tables); governance belongs in Cortex's existing ops DB.
- **Lightweight lineage** —
  - **[V]** `sqlglot.lineage()` — pure-Python column-level lineage from SQL ASTs + schemas; embeddable, no service. — https://sqlglot.com/sqlglot/lineage.html
  - **[V]** SQLMesh — builds column-level lineage automatically (uses sqlglot under the hood). — https://www.tobikodata.com/blog/column-level-lineage-for-dbt
  - **[V]** OpenLineage is the interchange standard; Marquez is the Postgres-backed reference server (a service — heavier); a `sqlmesh-openlineage` bridge appeared Jan 2026. — https://github.com/sidequery/sqlmesh-openlineage
  - **[I]** Verdict: compute lineage in-process with sqlglot; store edges in the ops DB; optionally *emit* OpenLineage-format events so future Marquez/enterprise integration is a config flag, not a rewrite.

---

## Verdicts for Cortex

1. **Table format: DuckLake 1.0.** Attach as `ducklake:sqlite:<ops-adjacent>.db` with `DATA_PATH` on local disk. Rationale: production-ready (Apr 2026), zero services, in-process ACID + time travel + schema evolution + per-file encryption, data inlining kills the small-file problem at 25k-row scale, scales to tens of GB of Parquet. Single dependency already in the stack (`duckdb>=1.5.2` — note: requires bumping Python floor concerns not at issue; duckdb wheels cover Python 3.10 on win_amd64).
2. **Catalog: SQLite now, Postgres later.** SQLite catalog supports multiple local processes (FastAPI workers + jobs). Flip the ATTACH URI to `ducklake:postgres:` (Supabase) for multi-user/remote — same data files, no migration.
3. **No datalake/warehouse split.** Bronze/silver/gold become schemas inside one DuckLake catalog. Keep SQLite ops DB for OLTP (mirroring Databricks' own Lakebase split).
4. **Ingestion: Auto Loader analog.** File-ledger table (path + size + mtime + content hash) → exactly-once incremental loads into bronze; permissive types + rescued-data column at bronze; LDP-style **expectations** (warn/drop/fail + quarantine table + metrics in an event log) gate bronze→silver.
5. **Lineage: sqlglot in-process**, edges stored in ops DB, OpenLineage-format event emission as an optional integration. Do NOT embed Unity Catalog OSS or Polaris.
6. **Interop/exit story:** ship "Export to Iceberg" (DuckLake→Iceberg copy, or DuckDB writes into the customer's UC/Snowflake Iceberg REST catalog) and "Export to Parquet" (trivial — data already is Parquet). Delta export possible via `deltalake` 1.6.x writer if a Databricks-Delta-specific handoff is demanded.
7. **Honest claims:** "A true lakehouse: one open format (Parquet + SQL catalog) with ACID, time travel, and schema evolution — Databricks-grade concepts, local-first, zero infrastructure." Say "serverless-by-architecture" (in-process, zero warm-up) — do not claim Databricks/Delta/Iceberg *runtime* compatibility; claim **Iceberg-compatible export**. Genie analog = NL answers grounded in catalog metadata, certified SQL, instructions, and sampled values, with "Trusted" badging for parameterized certified queries.

*Watch list (re-check ~Sept 2026): DuckLake v1.1 spec; DuckDB 1.6; delta-rs DV writes (issue #4079); PyIceberg v3 writes; DuckDB Iceberg writes without REST catalog.*
