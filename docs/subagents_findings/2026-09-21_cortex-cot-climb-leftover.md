# Cortex CoT climb leftover: like-with-like honesty (does not close #212)

- **Date:** 2026-09-21
- **Keywords:** crew, cot, climb, leftover, like-with-like, freeroute, gencfsm, #212, #227
- **Main idea:** Leftover on #212 after PR #216 and harness #227/PR #230. Think text is consumed in the SQL prompt; improve re-thinks. Like-with-like is the pinned 26 ids from DMS #180 @ d2f116a6, not the 5-item 40.00% fixture and not a label-only n==26. Harness consumes `measure_climb`. Status stays INCOMPLETE; #212 stays OPEN. No invented better %.
- **Verify:** `python -m pytest tests/test_crew/test_cot_climb.py tests/test_crew/test_prompt_harness_climb.py tests/test_crew/test_insights.py tests/dms/test_insights_api.py -q`
- **Does not prove:** live `:5000`/`:8020`; GitHub CI; #212 COMPLETE; live Studio score on the curated 26; GPU finetune; trained JEPA.
- **Cite:** Cortex#212 leftover. Consumes #216 cot_climb, #227 harness @ 3302b929, FreeRoute #211/#215. Off freeze #4/#41-#44. Did not close #212. Did not dual-own #223/#224/#225.
