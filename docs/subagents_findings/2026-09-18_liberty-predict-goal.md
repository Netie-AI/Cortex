# Liberty predict-goal plan language + proxy V(s,a,g) on Control+Crew

- **Date:** 2026-09-18
- **Keywords:** liberty, predict-goal, plan language, action_value, V(s,a,g), g2.2, control, crew, #225
- **Main idea:** Buyer liberty path set/predict-goal is plan language plus tabular/proxy G2.2 action_value V(s,a,g). Crew POST. Control GET-display. REFUSE invent-trained forecasts. JEPA absent still proxy-honest.
- **Verify:** `python -m pytest tests/test_crew/test_liberty_predict_goal.py tests/test_crew/test_liberty_seek.py tests/test_crew/test_appshell.py tests/test_crew/test_config_policy.py tests/dms/test_action_value.py -q`
- **Does not prove:** live `:5000`/`:8020`; GitHub CI; trained JEPA; #225 COMPLETE; climb % better than DMS #180.
- **Cite:** Cortex#225. Parent EPIC-LIBERTY #222. Soft-depends #223 + #224 @ 58c4adb8. Reuses G2.2 action_value. Off freeze #4/#41-#44. Did not dual-own freeze PRs.
