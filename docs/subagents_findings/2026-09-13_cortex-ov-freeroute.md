# Cortex OV FreeRoute as central AI layer

- **Date:** 2026-09-13
- **Keywords:** crew, freeroute, openvault, insights, generative-ask, identity, measured-route, #211
- **Main idea:** Insights / generative-ask that need a model go through OpenVault FreeRoute (local or cloud keys). Unarmed fail-closed. Measured pick among bundled families (DeepSeek+Qwen+...), not a hardcoded grok-only path. Stable `cortex:crew` identity; keys stay in OV. DMS #180 gen 57.69% vs exact 38.46% WRONG=0 @ d2f116a6 is citation only.
- **Verify:** `python -m pytest tests/test_crew -q`
- **Does not prove:** live `:5000` / `:8020`; live OV bake-off; CoT climb (#212); Excel/PPT; GitHub CI.
- **Cite:** Cortex#211. Depends on closed #196 and DMS #180 @ d2f116a6. Reused crew openvault arming. Did not attach freeze #4/#41-#44. Did not mint #42.
