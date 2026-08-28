# Excel + Power BI demo loop (Cursor capture)

**Date:** 2026-07-30
**Source:** Cursor Agent + audit cortex-dms-audit
**Topic:** tools | excel | powerbi | export

## Verdict

- Use existing Cursor `user-excel` MCP for verification (already wired).
- Do NOT install haris-musa/excel-mcp-server unless richer formulas/charts/pivots are needed.
- Power BI Modeling MCP (`@microsoft/powerbi-modeling-mcp`) is semantic-model only — not a warehouse connector. Host stdio; no useful Docker image yet.
- Export shape: UTF-8 CSV for Excel Copilot import; isolated single Parquet file for Power BI (never DuckLake folder — double-counts).
- DMS product code must not use openpyxl.save / xlsxwriter / to_excel; DuckDB COPY + operator Excel save-as is the compliant path.

## Copilot prompt pack (trial)

1. Check blank headers, duplicate IDs, missing values, inconsistent types.
2. Summarize revenue / order count / AOV by Region; reconcile totals.
3. Month-by-month trend; three largest changes with cites.
4. Top five customers by Revenue with share of total.
5. Flag Quantity<0, Unit Price=0, Revenue≠Qty×Price — do not change data.
6. Pivot Region × Category with Revenue.
7. Latest complete month vs prior by Region.
8. Three decision-useful findings with exact supporting values.

## Engine implications

- `/dms/brain/export` must return real allowlisted rows (fixed this session).
- Add `format=parquet` isolated snapshot under `DMS_EXPORT_DIR`.
- Measure Copilot usefulness only after answer-engine exclusions/prose are truthful.

cite: distill: skill_distill/captures/2026-07-30_cursor_excel-powerbi-demo-loop.md
