# Cortex CoT climb consumes FreeRoute (does not own it)

- **Date:** 2026-09-17
- **Keywords:** crew, cot, climb, freeroute, gencfsm, dag_runner, #212
- **Main idea:** CoT/route/improve for generative-ask lives in `CortexOS.crew.cot_climb` and calls the public FreeRoute adapter. It reuses gen_cfsm G1 + dag_runner. JEPA stays proxy. Unarmed fail-closed. DMS #180 57.69%/38.46% WRONG=0 is cited, not replaced. Insights `generative_ask` is not patched: PR #215 owns FreeRoute wiring.
- **Verify:** `python -m pytest tests/test_crew/test_cot_climb.py -q`
- **Does not prove:** Insights generate=true uses CoT (wiring waits on #215); live `:5000`/`:8020`; #211 COMPLETE; GitHub CI; Excel/PPT.
- **Cite:** Cortex#212. Consumes #214 public FreeRoute shapes. Leaves G1-G6 / AC2-AC3 to PR #215. Off freeze #4/#41-#44. Did not mint #42. Did not dual-own #213.
