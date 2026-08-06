```yaml
keywords: [FRTR-bench, xlsx-ingest, dms-query, contract-ask, airgpt-rag, openvault-not-rag]
main_idea: "OpenVault has no spreadsheet RAG; FRTR maps to AirGPT rag/ingest+answer, Cortex /dms/ingest/file→sync→/dms/query, DMS /v1/studio/ingest-batch→/v1/chat/ask"
models: [composer-2.5]
workflow: none
reuse: golden_rule
status: verified
cite: agent:98d7402f-40e2-485a-a0c1-1dea3327ced8
repo: multi
date: 2026-08-03
```

# Excel RAG API crosswalk (FRTR)

## Main idea

- OpenVault = secrets/mesh only (`GET /api/healthz`, vault ingest) — **not** a bench target.
- Cortex SQL brain: `POST /dms/ingest/file` (b64) → `POST /dms/lakehouse/sync-warehouse` → `POST /dms/query`.
- DMS product: `POST /v1/studio/ingest-batch` → `POST /v1/chat/ask` with `grounded_tables`.
- AirGPT: `tests/RAG/frtr_eval.py` / `ingest.ingest_paths` + `answer.answer` (hybrid SQL+RAG smoke 6/6 on frtr_0001).

## Keywords (search)

`FRTR-bench`, `xlsx-ingest`, `dms-query`, `contract-ask`, `airgpt-rag`, `openvault-not-rag`

## Golden rule (if reusable)

> For Excel QA demos: AirGPT hybrid SQL+RAG for spreadsheet cell/KPI questions; Cortex `/dms/query` for warehouse SQL brain; never route FRTR through OpenVault. Ingest Cortex xlsx as `{"filename","content_b64"}` with steward `X-API-Key`, then sync-warehouse before query.

## Verify

```bash
# Cortex query smoke (warehouse already loaded)
curl -s -X POST http://127.0.0.1:8010/dms/query -H "Content-Type: application/json" -d "{\"question\":\"how many rows in inventory?\",\"session_id\":\"frtr\"}"

# AirGPT FRTR smoke
cd D:\AirGPT && python tests/RAG/frtr_eval.py --workbook frtr_0001_quarterly-summary.xlsx --eval-only --space-id 6 --depth max
```

## Promote?

Yes — INDEX row for cross-repo routing before any Excel RAG Task spawn.
