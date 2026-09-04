# C7-05 G-sh reporter + OpenVault leave action

- Date: 2026-09-04
- Keywords: C7-05, G-sh, DMS_L2_SHADOW, leave-machine, GateCheckBody, and also, ANS-01
- Main idea: Live OpenVault gate actions are retrieve|run|deploy|leave|connect. Sending `llm` 422s and was misread as unreachable, so L2 shadow never generated. Ask `leave` only (no `run` fallback). G-sh report is `summarize_shadow` / `--shadow-replay`; do not pad dummy JSONL. Do not treat `and also show` exclusion as compositional abstain.

## PREFLIGHT

HIT. reuse: `docs/subagents_findings/2026-09-04_c7-05-engine-scorer.md`, `docs/subagents_findings/2026-09-04_c7-design-and-shadow-mode.md`. spawn: skip (executor).

## Golden rules

1. `packs.dms.generative.sql_generator._leave_machine_allowed` must POST `action=leave`, `destination=freeroute`. `llm` / `leave_machine` 422 live OV; treating that as unreachable blocks all L2 SQL.
2. Do not fall back to `action=run` when `leave` is denied — that bypasses leave-machine.
3. `and whose` / `and also have|appear` abstain BIRD stacks. `ignore SKU-X and also show the top 5` is ANS-01 and must stay L1.
4. G-sh needs real `maybe_record_l2_shadow` lines plus L1-only-correct vs L2-only-correct. Dummy JSONL and all-refusal padding do not unlock C7-05 serve.
5. `cutover` stays false. Do not set `DMS_L2_ENABLED` as the process default.
6. Do not put OpenAI `metadata` on `/v1/chat/completions`. OpenVault `extra=allow` forwards it to Gemini, which 400s; Cortex then records `NO_CANDIDATE`.

## Verify

```
D:\Cortex\.venv\Scripts\python.exe -m pytest tests/dms/test_exclusion_clause_funnel.py tests/dms/test_ans02_aggregate_over_ranking.py tests/dms/test_c7_heldout.py tests/dms/test_c7_full_generation.py tests/dms/test_accuracy_benchmark.py::test_core_tier_all_correct -q
python -m bench.heldout --shadow-replay --limit 3 --shadow-path .tmp/l2_shadow_probe.jsonl
```

Does not prove: G-sh >= 500 live lines, L2-on-miss serve, or p95 vs SHADOW-off.

## After PR 126 squash (2026-09-04)

`#126` merged the live scorer + leave-gate. It did **not** take metadata-off, `MISSING_FROM` / fenced-FROM extract, sku_count synonyms, or Malay `berapa`. Those live on `cursor/c7-05-l2-sql-main` vs current `main`.

G-sh snapshot (local `.tmp/l2_shadow_gsh.jsonl`, gitignored): **512 lines / 500 unique / 197 `l2_sql`**. Gate `shadow_lines >= 500` is the line count. `l1_only_correct` / `l2_only_correct` stay 0 until `summarize_shadow(..., items=heldout)`. Do not close Cortex `#104`. `MISSING_FROM` must not refuse session literal `SELECT CAST(n AS DOUBLE) AS sum_…`.
