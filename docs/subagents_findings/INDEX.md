# Subagent findings â€” Cortex (canonical)

**Purpose:** Before any subagent spawn, scroll this INDEX. Store every finding here so Cortex/Netie can learn and smaller agents can reuse golden rules.

**Mirrors:**

| Product | Path |
|---------|------|
| Claude Code | `C:\Users\OoiJianHong\.claude\subagents_findings\` |
| Cursor | `C:\Users\OoiJianHong\.cursor\subagents\` (lenses) + `findings\` |

**Invoke guides:**

- Claude: `C:\Users\OoiJianHong\.claude\INVOKE_SUBAGENTS.md`
- Cursor: `C:\Users\OoiJianHong\.cursor\INVOKE_SUBAGENTS.md`

## Index

| Date | Topic | Keywords | Main idea | Path |
|------|-------|----------|-----------|------|
| 2026-08-05 | dms-accuracy-research-ingest | text-to-sql, accuracy, bigtable, semantic-layer, freeRoute, trusted-assets, bird, chess, mac-sql, din-sql, crag, genie, precision-on-answered, epic-017..020 | Bigtable teaches storage/ops only; Text-to-SQL accuracy comes from verified assets + semantic metrics + execution-verify loops (Snowflake/Genie/CHESS/MAC-SQL/DIN-SQL/CRAG), mapped onto EPIC-017..021 — never Cassandra, MCP-in-customer-DB, or regex synonym dictionaries as primary coverage. | `2026-08-05_dms-accuracy-research-ingest.md` |
| 2026-08-05 | dms-serving-warehouse-verdict | duckdb, serving_engine, postgres, cassandra, mssql, dynamodb, mcp, iceberg, five-ports, bronze-writer | Keep DuckDB as serving warehouse; enterprise DBs enter via bronze connectors (or later serving_engine swap) — never Postgres-as-OLAP, Cassandra, or MCP ontology-in-customer-DB. | `2026-08-05_dms-serving-warehouse-verdict.md` |
| 2026-08-03 | openhbm-jesd-falsegreen | jesd270-4a, hbm4, clean-room, false-compliance, false-green, assert-True, coverplan | OpenHBM overclaims JESD270-4A compliance while leaf-IP cocotb suites and agent-eval func_cov can green without proving the named behavior | `2026-08-03_openhbm-jesd-falsegreen.md` |
| 2026-08-03 | excel-rag-api-crosswalk | FRTR-bench, xlsx-ingest, dms-query, contract-ask, airgpt-rag, openvault-not-rag | OpenVault has no spreadsheet RAG; FRTR maps to AirGPT rag/ingest+answer, Cortex /dms/ingest/file→sync→/dms/query, DMS /v1/studio/ingest-batch→/v1/chat/ask | `2026-08-03_excel-rag-api-crosswalk.md` |
| 2026-08-03 | frtr-excel-rag-playbook | excel-rag, FRTR-bench, SQL-agent, SiRA, spreadsheet, openpyxl, cell-provenance, hybrid-retrieval, KPI-boost | Flat embed of 4M FRTR cells fails; SQL-per-workbook + schema/cell index + Questions holdout + data_only resolver is the ponytail path to ~85%+ accuracy; xlsx embedded images are the main multimodal gap | `2026-08-03_frtr-excel-rag-playbook.md` |
| 2026-08-03 | secrets-dms-ci-doc-foundation | CORTEX_CONTRACT_TOKEN, org-secrets, mypy, dependabot, feedback-ledger | Org secrets miss private repos; repo token unblocks checkout then mypy; ledger lives in Netie PRD | `2026-08-03_secrets-dms-ci-doc-foundation.md` |
| 2026-08-01 | estate-audit-false-greens-and-identity | false-green, import-linter, eval-corpus, manifest-bypass, wasm, auth-default, cortex_contract, module-identity, osr, gen_cfsm, space-acl, plane-taxonomy | Three gates report green while structurally unable to fail; `enforce_manifest` is bypassed on `/dms/query`; `cortex_contract` resolves as two module identities of one file | `2026-08-01_estate-audit-false-greens-and-identity.md` |
| 2026-07-31 | pointer-beat-realtime | gemini, C25-01, voice, skills | Pointer Beat Realtime: Gemini-first, computer-use scaffold, SkillCards; findings in D:\Pointer\docs\subagents_findings | `../Pointer` cross-ref |
| 2026-07-31 | demo-clear-exclusion-c5-c8 | drillthrough, exclusion, clarify, C5, C8, wolf | Demo blockers + exclusion confirm + C5/C8 mins; C7 schema-gate next | `2026-07-31_demo-clear-exclusion-c5-c8.md` |
| 2026-07-31 | routing-gaps-eval-surfaces | routing, malay, delayed_count, E4, ontology, trust, 1a, handoff | Live routing+E4 fixed; Ontology/Trust reviewed; next Phase 1b + I1 importlinter | `2026-07-31_routing-gaps-eval-surfaces.md` |
| 2026-07-31 | c7-plausibility-failopen-space-leak | C7, plausibility, route_to_metric, confidently_wrong, fail-open, compliance_gate, space ACL, cross-space leak, A-0004, postgres, RLS | Assert the customer artifact: a keyword branch answering a different question, 9 copy-pasted fail-open write gates, and a Space boundary with no production caller | `2026-07-31_c7-plausibility-failopen-space-leak.md` |

## How to add

1. Copy `_TEMPLATE.md` â†’ `YYYY-MM-DD_<kebab>.md`
2. Fill keywords + main_idea **first**
3. Append a row here
4. Mirror to `~/.claude/subagents_findings/` if Claude Code should see it offline
