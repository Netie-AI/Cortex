# Finding: Cortex #211 NARROW honest plan_source

- **Date:** 2026-09-25
- **Keywords:** insights, generative-ask, plan_source, ontology_plan, studio, #211, #231, #196
- **Main idea:** Cortex Insights never stamped `plan_source` or `query_sql`. DMS Studio (`plan_source_from_payload`) treats missing as `other`, so live #231 measured ontology_plan=0/52. Stamp `ontology_plan` only when generate SQL is NL then ontology plan then FreeRoute then validate. Engine L0/L1 and unarmed stay `other`. Request `mode=ontology_plan` is not a stamp.
- **Verify:** `python -m pytest tests/test_crew/test_insights_plan_source.py tests/test_crew/test_insights.py tests/dms/test_insights_api.py -q`. HTTP InsightsAskIn must stay the frozen OpenAPI 1.2.0 property set (no extra query_plan/mode fields; extra JSON ignored).
- **Does not prove:** live Studio DMS #231 re-prove (ontology_plan > 39/52 WRONG=0); GitHub CI until push checks run; #211 broader FreeRoute-central COMPLETE; DMS `live_ask` closed compute seam (GEN-03) calling Insights.
- **Cite:** Cortex#211 Epic narrow comment 5825880807. Reused #196 Insights + FreeRoute. Off freeze #4/#41-#44. Did not close #211.
