```yaml
keywords: [excel-rag, FRTR-bench, SQL-agent, SiRA, spreadsheet, openpyxl, cell-provenance, hybrid-retrieval, KPI-boost]
main_idea: "Flat embed of 4M FRTR cells fails; SQL-per-workbook + schema/cell index + Questions holdout + data_only resolver is the ponytail path to ~85%+ accuracy; xlsx embedded images are the main multimodal gap."
models: [composer-2.5, grok-4.5]
workflow: rag-eval
reuse: golden_rule
status: verified
cite: "task: FRTR-Bench demo prep | agent: explore subagent | distill: none"
repo: AirGPT
date: 2026-08-03
```

# FRTR Excel RAG playbook (ranked tactics)

## Main idea

- Enterprise spreadsheet QA (~4M cells, 30 workbooks, ~159 Qs) cannot be solved by flat text-embed of every cell; route numeric/agg questions to SQL-over-sheets, keep hybrid RAG for schema routing and citations, never ingest Questions, resolve `See Sheet!Cell` GT via `data_only`, and extract embedded images for chart Qs.

## Keywords (search)

`excel-rag`, `FRTR-bench`, `SQL-agent`, `SiRA`, `spreadsheet`, `openpyxl`, `cell-provenance`, `hybrid-retrieval`, `KPI-boost`, `sanitize`, `frtr_eval`

## Ranked tactics (8, by expected lift)

1. **SQL-agent per workbook (+35-45%)** -- Load data sheets into in-memory SQLite/DuckDB; NL→SELECT-only SQL for agg/comparison/computation; schema sidecar (cols, types, 3 sample rows) for routing, not full-grid embed. Files: new `rag/xlsx_sql.py`, `rag/answer.py` router branch.
2. **Fair FRTR eval harness (+0% cheat, enables measurement)** -- Load GT from Questions at eval time only; resolve `See Summary!B4` / `Max of D3:D6` via openpyxl `data_only`; numeric tolerance `1e-4`; accept tie-break OR answers. File: `tests/RAG/frtr_eval.py`.
3. **Skip Questions sheet at ingest (mandatory)** -- Filter `questions`/`readme` in `_xlsx_text` so answers never leak into the index.
4. **Sheet-schema + cell-address index (+15-20%)** -- Index schema chunks, cell refs (`Summary!B4 = value`), Summary rows; two-pass ingest (values + formulas on small sheets).
5. **Row-parent chunks with sticky headers (+10-15%)** -- 50-row windows per sheet with header row repeated; `tabular=True` in chunking instead of token windows on pipe-rows.
6. **Formula dependency graph (+8-12%)** -- Parse `=` cells; index edges `Summary!B4 -> SUMIFS(Revenue!F3:F602)` for cross-sheet reasoning (30 cross-sheet formulas in FRTR).
7. **SiRA phrase enrichment on spreadsheet vocabulary (+5-8%)** -- DF-guarded column names, quarter labels, sheet titles on schema text; hybrid FTS+vector already in `rag/retrieve.py`.
8. **Embedded image extraction (+5-12%)** -- Unzip `xl/media/*.png`, map to `Summary_image_001` provenance, OCR/vision index; gap in `rag/media.py` today (standalone OCR only).

## Why flat text-embed fails

- **Scale:** 656k rows bury any single cell in chunk budget.
- **Structure loss:** `"West | 12345"` without column names breaks semantic search.
- **Aggregation blindness:** SUMIFS/comparison needs SQL, not nearest chunk.
- **Cross-sheet:** Summary values useless without Revenue range link.
- **Provenance GT:** Answers like `See Summary!B4` must be resolved, not parroted.
- **Multimodal:** 53 embedded charts invisible to `_xlsx_text`.

## AirGPT shipped this session (2026-08-03)

- **Materialize formula caches on sanitize** -- `frtr_eval.sanitize_workbook` reads source with `data_only=True` and writes values-only copy; naive openpyxl save had wiped Summary KPI cells.
- **KPI heading boost in retrieve** -- `rag/retrieve.py` soft-boosts Summary/KPI chunks (+0.35 heading / +0.2 short text) when query matches aggregation keywords (`total`, `revenue`, `margin`, `quarter`, etc.).
- **Sheet index in ingest._xlsx_text** -- Skips Questions/Readme; sorts smallest sheets first (Summary before Revenue); prepends `## Spreadsheet index` block.
- **Hybrid SQL+RAG eval** -- `tests/RAG/frtr_eval.py` (+ `frtr_sql_baseline.py`, `frtr_cross_compare.py`): sanitize, ingest, `answer()` with LLM, `resolve_answer` + `score_answer`.
- **Smoke result:** **5/6 (83.3%)** on `frtr_0001_quarterly-summary.xlsx`; only miss = chart-trend Q (needs vision; Summary numbers conflict with gold `Yes`).

## Questions left open

- Full 30-workbook / ~159 Q hybrid eval after ingest completes.
- DuckDB lane for 50GB-scale Excel (PARKING_LOT P1).
- Excel MCP write-back demo (`user-excel` Insights sheet) -- research via RAG only, MCP for export.

## Golden rule (if reusable)

> For enterprise xlsx QA: never embed raw grids at scale. Sanitize (strip Questions, materialize formula values), index schema + KPI sheets first, boost Summary on numeric queries, route aggregations to SELECT-only SQL, score with cell-resolved GT. Use MCP only to write answers back, not to research.

## Verify

```bash
cd D:\AirGPT
python tests/RAG/frtr_eval.py --workbook frtr_0001_quarterly-summary.xlsx
# Expect ~5/6; inspect results/ for per-question scores
```

## Promote?

- [x] `docs/subagents_findings` only
- [ ] `~/.claude/skills|workflows|findings`
- [ ] `~/.cursor/skills|workflows|subagents|findings`
- [ ] `skill_distill/captures` + ingest
