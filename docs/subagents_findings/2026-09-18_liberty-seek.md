# Liberty seek on Control+Crew

- **Date:** 2026-09-18
- **Keywords:** liberty, seek, g2.1, seeker, control, crew, fail-closed, #223
- **Main idea:** Buyer surface for G2.1 `seeker.seek`. Crew POST starts governed proactive seek with an F1 audit trail. Control GET-displays. No goal / missing extra REFUSE. execute=true PARK with executed=[]. No parallel autonomy stack. JEPA stays proxy.
- **Verify:** `python -m pytest tests/test_crew/test_liberty_seek.py tests/test_crew/test_appshell.py tests/test_crew/test_config_policy.py tests/dms/test_engine_seek.py -q`
- **Does not prove:** live `:5000`/`:8020`; GitHub CI; #223 COMPLETE; trained JEPA; predict-goal; climb % better than DMS #180.
- **Cite:** Cortex#223. Parent EPIC-LIBERTY #222. Reuses G2.1 seeker. Off freeze #4/#41-#44. Did not dual-own #224/#225.
