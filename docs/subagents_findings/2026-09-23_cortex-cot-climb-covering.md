# Cortex CoT climb covering increment (does not close #212)

- **Date:** 2026-09-23
- **Keywords:** crew, cot, climb, covering, exact, g1, freeroute, gencfsm, #212
- **Main idea:** Next covering increment after leftover honesty. Think/SQL consume the G1 `generate_ir` plan; improve re-thinks with prior SQL + `route_step`. `measure_climb` reports this_run.exact vs certified gold when id/expected_sql is present. Fixture 40.00% gen / 0.00% exact and pinned-26 empty 0.00% gen / 0.00% exact do not replace DMS #180 gen 57.69% / exact 38.46% WRONG=0 @ d2f116a6. Status stays INCOMPLETE; #212 stays OPEN. No invented better %. Unarmed fail-closed REFUSE; validated SQL ABSTAIN.
- **Verify:** `python -m pytest tests/test_crew/test_cot_climb.py tests/test_crew/test_prompt_harness_climb.py tests/test_crew/test_insights.py tests/dms/test_insights_api.py -q`
- **Does not prove:** live `:5000`/`:8020`; GitHub CI; #212 COMPLETE; live Studio score on the curated 26; GPU finetune; trained JEPA; RELEASE.
- **Cite:** Cortex#212 covering. Consumes leftover honesty @ c6df0d7c, #216 cot_climb, #227 harness, FreeRoute #211/#215. Off freeze #4/#41-#44. Did not close #212. Did not dual-own #223/#224/#225.
