```yaml
keywords: [text-to-sql, accuracy, bigtable, semantic-layer, freeRoute, trusted-assets, bird, chess, mac-sql, din-sql, crag, genie, precision-on-answered, abstain, value-normalization, epic-017, epic-018, epic-019, epic-020, duckdb]
main_idea: "Bigtable teaches storage/ops only; Text-to-SQL accuracy comes from verified assets + semantic metrics + execution-verify loops (Snowflake/Genie/CHESS/MAC-SQL/DIN-SQL/CRAG), mapped onto EPIC-017..021 — never Cassandra, MCP-in-customer-DB, or regex synonym dictionaries as primary coverage."
models: [grok-4.5]
workflow: none
reuse: golden_rule
status: raw
cite: task: dms-accuracy-research-ingest | paper: Baltieri et al. SIGMOD Companion 2026 Twenty Years of Bigtable (3788853.3803095)
repo: multi
date: 2026-08-05
```

# DMS/Cortex accuracy — research ingest (Bigtable + Text-to-SQL)

PREFLIGHT: PARTIAL — reuse `2026-08-05_dms-serving-warehouse-verdict.md` (DuckDB keep; Cassandra/MCP anti-patterns) + `2026-08-03_frtr-excel-rag-playbook.md` (SQL-per-workbook + provenance). This file is the accuracy-path ingest.

## Main idea

- Bigtable (Baltieri et al., SIGMOD Companion 2026) is a **storage/ops** experience paper; it does not move Text-to-SQL precision.
- Industry accuracy lifts come from **semantic/metric layers + verified queries + generate→execute→refine**, not engine swaps.
- Promote via **EPIC-017→018→019→020→021** on the existing Cortex HTTP + DuckDB path; park sixth ports and Cassandra.
- **F31:** adaptive Gen/C/JEPA/FSM/DAG chooser is Cortex gen-cFSM / P21 research — DMS ask path stays linear verify until EPIC-018 shows a bottleneck. Hostile pack under SCORE-01 is the founder break-test surface.

## Keywords (search)

`text-to-sql`, `accuracy`, `bigtable`, `semantic-layer`, `FreeRoute`, `trusted-assets`, `BIRD`, `CHESS`, `MAC-SQL`, `DIN-SQL`, `CRAG`, `Genie`, `precision-on-answered`, `abstain`, `value-normalization`, `EPIC-017`, `EPIC-018`, `EPIC-019`, `EPIC-020`

## Questions left open

- NL unit-test agent (CHESS UT) vs keep EXPLAIN + exec + envelope E1–E9 only.
- Self-consistency majority vote budget under FreeRoute `auto` (cost vs lift).
- When EPIC-021 YAML metric pack is unlocked after first real EPIC-020 customer schema.

---

## A. Bigtable paper → DMS (storage/ops only) — 5 bullets

Source: Baltieri et al., *Twenty Years of Bigtable*, SIGMOD Companion 2026 (PDF `D:\DMS\3788853.3803095.pdf`).

1. **Durable core + cheap moves:** WAL/commit log + LSM SSTables; tablet data on distributed FS (Colossus) so moves are metadata, not byte copies → keep DuckDB/Parquet (or later Iceberg) as serving files with **single-writer**; do not chase live tablet rebalance as an accuracy fix.
2. **Secondary work off the foreground write path:** replication, CDC changelogs, MVs, external compactors are async / not on the critical write → DMS **bronze→silver promote / quarantine** stays off the ask path; never fold compaction/promote into `/v1/chat/ask`.
3. **Verify-before-install:** SSTables read back, checksummed, parsed **before** metadata install; gap detection blocks GC → mirrors **manifest + quarantine + promote gate** before facts are answerable; refuse to serve unvalidated layers.
4. **Bulk build then install metadata:** bulk import builds SSTables offline, then tablet servers only update metadata; offline/batch bypasses serving nodes → **EPIC-020 connectors + bulk bronze** should build artifacts offline; serving DuckDB stays the ask calculator.
5. **Operate as one service with fixed shapes + backups-by-default + probe SLIs:** SRE service model, standardized server shapes, daily backups, blackbox latency/availability probers, serve vs batch isolation → DMS stack ops (compose pin, `Start-DMSStack`, backup defaults, ask SLIs) — not per-customer DIY warehouses.

### Explicit: what does NOT transfer to Text-to-SQL accuracy

- Wide-column / eventual consistency / multi-primary replication / CRDTs / counters.
- Schemaless "flexible storage" as a substitute for metrics, joins, or value encoding.
- Cassandra/HBase lineage or "scale like Bigtable" as an answer-quality argument.
- Cloud Bigtable SQL / MVs as ontology (analytics features ≠ governed business metrics).
- Cache right-sizing, autoscaler TCO, 10EB/QPS war stories — throughput, not precision-on-answered.
- Anything that would replace DuckDB or add a sixth port "because Google runs Bigtable."

---

## B. Transferable accuracy techniques (AI/DB lit → DMS/Cortex)

| # | Technique (concrete system) | Map |
|---|-----------------------------|-----|
| 1 | **Semantic / metric layer** — Snowflake Cortex Analyst BIRD lift **57%→78%** same LLM; Databricks Genie **Metric Views** (YAML measures/dims/synonyms) | **EPIC-021** (parked until EPIC-020 lands a real schema). Near-term subset = ontology pack fields already in Cortex, not a new port. |
| 2 | **Verified query repository** — Snowflake `verified_queries`; Genie **Knowledge Store** (example SQL + instructions) | **EPIC-019** trusted assets / L0–L1 certified path. Highest ROI coverage without inventing SQL. |
| 3 | **Decompose + self-correct** — **DIN-SQL** (schema link → sub-questions → self-correction) | **EPIC-012** FreeRoute verifier loop (**CLOSED**). Keep; do not reopen for vanity agents. |
| 4 | **Selector → Decomposer → Refiner** — **MAC-SQL** (schema prune; CoT sub-Q; exec feedback refine) | Existing: schema retrieval + L2 generate + `sql_validate_gate` + DuckDB exec errors. **Refiner = linear retry**, not a new agent framework. |
| 5 | **IR / Schema Selector / Candidate Gen / Unit Tester** — **CHESS** (BIRD ~71% high-budget) | IR+SS ≈ retrieval/prune; CG ≈ FreeRoute; **UT = gap/park** (NL unit tests beyond EXPLAIN/empty-check) unless EPIC-018 shows residual false-greens. |
| 6 | **Sample → execute-filter → cluster** — DeepMind **AlphaCode** (trust execution over model judgment) | FreeRoute multi-sample + execute filter + abstain if none pass. **Park** million-sample budgets; keep small-N under OpenVault `auto`. |
| 7 | **Self-consistency / exec majority** — DAIL-SQL-SC style voting on candidates | Partial today (model rotate on gate fail). **Gap/park:** majority vote on execution fingerprints when cost allows. |
| 8 | **Value / literal grounding** — high-cardinality lookup (Snowflake Cortex Search pattern); DMS hard rule 12 | **Law already** (`BETA` vs `SKU-BETA`). **Gap:** wire literal semantic search into L2 generation (PRD already names this). |
| 9 | **Retrieval confidence gate** — **CRAG** (Correct / Incorrect / Ambiguous → correct or refuse) | **EPIC-015 L3:** low-confidence retrieve → abstain or hand off to L2; **never** mint aggregates from doc prose (F26 AirGPT failure mode). |
| 10 | **Precision-on-answered + coverage instruments** — product law; Snowflake/dbt eval culture | **EPIC-017** numeric/envelope boundary; **EPIC-018** precision-on-answered + coverage reports. Ship before chasing BIRD %. |
| 11 | **Type boundary: SQL owns numbers, RAG owns prose** — PRD L0/L1/L2 vs L3 | Existing product law. Enforce with EPIC-017 E9; L3 citations only. |
| 12 | **Abstain / clarify over invent** — Genie "clarification over guessing"; DMS 0-confidently-wrong | Law. **Gap/park:** clarify UX + similar trusted questions (coverage without lying). |

---

## C. Explicit anti-patterns

1. **Regex synonym dictionaries as primary coverage** — brittle, silent miss → plausible wrong filters (worst failure class). Synonyms belong in curated semantic/metric YAML or trusted assets, versioned and eval'd — not a growing regex bag as the main ladder.
2. **MCP ontology / inference inside the customer DB** — MCP may orchestrate sync/ops; must **not** host ontology, routing, or answer inference over live MSSQL/Dynamo (see F27 + serving-warehouse verdict). Breaks F5/manifest/ledger and invents outside the envelope.
3. **Cassandra (or Scylla) "for accuracy"** — wrong data model for governed metric SQL; adds a store outside five ports; Bigtable's own paper is **ops scale**, not Text-to-SQL. Declined (P-DMS-28). Accuracy ceiling is routing + values + verified metrics, not wide-column QPS.

Also refuse: replacing DuckDB to "feel enterprise"; sixth port; RAG summing sampled cells as warehouse truth (F26).

---

## D. Recommended Cortex orchestrator shape

**Prefer a linear verify loop over a fat multi-agent DAG.**

```
route → (L0/L1 trusted hit?) 
      → else FreeRoute: generate SQL → sql_validate_gate → DuckDB exec 
      → refine ≤N on syntax/empty/gate fail → rotate model if needed
      → else L3 doc-RAG (CRAG-style retrieve gate) OR abstain
      → insights only on grounded rows
      → assert customer envelope (badge/abstain/values/sources/…)
```

| Layer | Owns | Does not own |
|-------|------|--------------|
| **Golden rules / verification** | Sit **after** candidate SQL and **before** customer envelope: validate SQL, execute, value-norm checks, numeric boundary (EPIC-017), envelope E1–E9. | Model vibes; RAG prose totals. |
| **FreeRoute (L2)** | Candidate SQL generation, model rotate, bounded refine on exec/gate feedback. | Deterministic coverage; doc aggregates. |
| **Trusted assets (L0/L1, EPIC-019)** | Exact/semantic match → return certified SQL/results; coverage growth. | Open-ended exploration (that's L2). |
| **RAG (L3, EPIC-015)** | Space-scoped hybrid retrieve + citations/prose after SQL abstain; CRAG-style refuse on bad retrieve. | Any numeric aggregate that belongs in SQL. |

MAC-SQL/CHESS roles collapse to **functions in one loop**, not three always-on agents. DAG only if a later measured need (e.g. parallel candidate gen) beats linear refine on EPIC-018 scores.

---

## E. Ordered 5-step research → product promotion (no sixth port, keep DuckDB)

1. **Measure the law** — Close/ship **EPIC-017** (numeric answer boundary / E9) + **EPIC-018** (precision-on-answered + coverage). No BIRD chase without this scoreboard.
2. **Curate coverage** — **EPIC-019** verified/trusted query pack (Genie Knowledge Store / Snowflake verified_queries pattern) in Studio + Cortex ontology — deterministic wins before more sampling.
3. **Wire value normalization into L2** — hard rule 12 into generation/retrieval of literals (stop empty-match green badges). Smallest code change with largest false-green kill rate.
4. **Land facts, don't relocate the brain** — **EPIC-020** `sql_source` (MSSQL/MySQL → bronze → silver → DuckDB serve). Customer DB is source; DuckDB remains `serving_engine`.
5. **Then semantic metrics** — **EPIC-021** customer-shaped metric/semantic YAML on a real synced schema (Snowflake 57→78% lesson). Add CRAG-style L3 confidence gate. Iceberg/serving swap only on measured writer contention (P-DMS-17) — still one `serving_engine` port.

---

## F. Founder answer (one paragraph)

MSSQL-native ontology feels easier because the tables already live where the buyer works, and "put the brain in the ERP" sounds like zero-ETL honesty — but it fails the accuracy law: ontology and inference inside the customer DB bypass Cortex F5/manifest/ledger, mix OLTP operational encoding with analytical metrics, and turn every dialect cast / synonym / empty filter into a confidently wrong number with no single envelope to assert. Build instead what the literature and your own EPICs already name: **sync facts into bronze (EPIC-020), serve SQL on DuckDB, put metrics + verified queries in Cortex/DMS ontology packs (EPIC-019→021), generate freeform SQL only behind FreeRoute's validate→execute→abstain loop, and keep RAG as prose after SQL abstains** — precision-on-answered (EPIC-017/018) as the scoreboard, never Cassandra, never MCP-as-warehouse, never regex dictionaries as primary coverage.

## Golden rule (if reusable)

> Accuracy ≠ warehouse engine. Keep DuckDB; promote verified assets + value-norm + semantic metrics via EPIC-017→021; FreeRoute is a linear exec-verify loop; L3 never invents totals; reject Cassandra / MCP-in-customer-DB / regex-synonym-primary.

## Verify

```bash
# Read-only ingest — no product code change required.
# Cross-check EPIC map:
rg -n "EPIC-017|EPIC-018|EPIC-019|EPIC-020|EPIC-021|precision-on-answered" "D:\Netie\Software Blueprint\DMS\PRD-001-governed-answers-over-your-own-data.md"
# Sibling anti-pattern finding:
rg -n "Cassandra|MCP|DuckDB" "D:\Cortex\docs\subagents_findings\2026-08-05_dms-serving-warehouse-verdict.md"
```

## Promote?

- [x] `docs/subagents_findings` only
- [ ] `~/.claude/skills|workflows|findings`
- [ ] `~/.cursor/skills|workflows|subagents|findings`
- [ ] `skill_distill/captures` + ingest
