# Liberty proxy JEPA collapse_score on Control+Crew

- **Date:** 2026-09-18
- **Keywords:** liberty, jepa, collapse_score, proxy, gen_cfsm, seek, #224
- **Main idea:** Buyer liberty path collapses G2.1 seek candidates with G1 `gen_cfsm.collapse_score` (proxy cosine). Stamps proxy/not-trained; refuses invent-trained WM COMPLETE. Not a trained world model.
- **Verify:** `python -m pytest tests/test_crew/test_liberty_seek.py tests/dms/test_engine_seek.py tests/test_crew/test_appshell.py -q`
- **Does not prove:** live `:5000`/`:8020`; GitHub CI; trained JEPA; #224 COMPLETE; predict-goal; climb % better than DMS #180.
- **Cite:** Cortex#224. Parent EPIC-LIBERTY #222. Soft-depends #223 @ f1431414. Reuses G1 collapse_score. Off freeze #4/#41-#44. Did not dual-own #225.
