# Prompt+harness climb vs DMS #180 (ideas-only distill)

- **Date:** 2026-09-18
- **Keywords:** prompt, harness, climb, freeroute, distill, cot, #227, #212
- **Main idea:** Crew `prompt_harness_climb` measures generative-ask / CoT quality against frozen DMS #180 (gen 57.69% / exact 38.46% WRONG=0 @ d2f116a6) using FreeRoute free/normal models. Distill (D) is ideas-only -- no GPU finetune. Fixture 40.00% is not like-with-like and does not replace the baseline. Status stays INCOMPLETE; #212 remains OPEN.
- **Verify:** `python -m pytest tests/test_crew/test_prompt_harness_climb.py tests/test_crew/test_cot_climb.py tests/test_crew/test_insights.py tests/dms/test_insights_api.py -q`
- **Does not prove:** live `:5000`/`:8020`; GitHub CI; #212 COMPLETE; like-with-like climb vs the curated 26; GPU finetune; trained JEPA.
- **Cite:** Cortex#227. Consumes #212 cot_climb @ d2a19728, FreeRoute #211/#215 @ 50267289, distill_harness RSF-06. Off freeze #4/#41-#44. Did not close #212. Did not dual-own #223/#224/#225.
