---
keywords: [drillthrough, exclusion, clarify, C5, C8, demo, T7, wolf, provenance]
main_idea: Demo blockers cleared — drillthrough warehouse fallback, exclusion confirm chip, C5/C8 mins shipped; C7 schema-gate product hardening still next.
models: [composer-2.5, grok]
workflow: none
reuse: golden_rule
status: verified
cite: session:dms-demo-then-ct
---

# 2026-07-31 — demo clear + C5/C8

## Main idea

- **Drillthrough 500:** rewrite projected `_src_*` provenance onto warehouse tables that lack them; nested `ROUND(COALESCE(SUM(...)))` was not stripped. Fixed with provenance fallback + recursive aggregate unwrap. Live token → 200 + rows.
- **Exclusion clarify:** fuzzy `sku_name` resolve ("wolf" → SKU-00175 Alha Wolf Pack); ABSTAIN with Yes/No suggestions; DMS UI 5s auto-No. Exact encoding (BETA→SKU-BETA) still applies immediately. Exclusion verb + unresolved → never query-skill.
- **C5-min:** ToolClass on ontology tools; agent→apply refused.
- **C8-min:** durable `query_run` SQLite at `data/engine/query_run.db`.
- **Keep** regenerated `data/samples/` (SKU-BETA + Alha Wolf Pack).
- **Parked:** CRAG/BIRD/Postgres host publish/OpenVault merge.

## Questions left open

- C7 schema-gate product hardening (retrieval→EXPLAIN→retry) beyond Protocol port
- claim_n still 47 until `verify_gold --review`
- DMS worktree predictive/gate merge into main `D:\DMS` when owner asks

## Verify

```bash
python -m pytest tests/test_execution/test_drillthrough.py tests/dms/test_exclusion_clarify.py tests/test_execution/test_tool_class_c5.py tests/test_execution/test_query_run_c8.py -q
# live: remove wolf from the top 5 sales → ABSTAIN + Yes chip
# live: drillthrough after Top 5 → 200
```
