# Cortex API surface for DMS and AirGPT

- **Date:** 2026-09-17
- **Keywords:** api, insights, generative-ask, airgpt, freeroute, openvault, #213
- **Main idea:** Engine `GET|POST /v1/insights` is the stable Cortex API DMS generative-ask and AirGPT skin call. Same `run_insights` as Crew. CERTIFIED|ABSTAIN|REFUSE. OV/FreeRoute holds keys; callers send ov_ or loopback. Local vs cloud posture is documented without secrets. Sibling to `/v1/contract`, not a cortex-contract bump.
- **Verify:** `python -m pytest tests/dms/test_insights_api.py tests/test_freeroute_core.py tests/packaging/test_profile_routes.py tests/test_crew/test_insights.py -q`
- **Does not prove:** live `:5000`/`:8020`; GitHub CI; #213 COMPLETE; CoT COMPLETE; climb % better than DMS #180; G4 env-key hosts.
- **Cite:** Cortex#213. Consumes #211 @ 50267289 and #212 @ d2a19728. Off freeze #4/#41-#44. Did not mint #42. Did not dual-own DMS issues.
