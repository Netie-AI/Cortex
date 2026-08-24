# 2026-08-23 -- Cortex Crew desktop + OpenVault keys

Keywords: crew, openvault, freeroute, cursor-key, computer-control-off, prefix-cache, gemini

Main idea: Cortex Crew is a local desktop app on :8020 (Edge --app), not a website. Computer control stays off. Model calls go through OpenVault FreeRoute (`openvault/auto`). The seeded Cortex primary key (HTTP 404, non-retryable) must stay disabled so Groq/Google/OpenRouter walk. Cursor API key was not on disk in D:\Cortex-crew; paste it in Providers to vault it forever via POST /api/keyvault/upsert.

Golden rule: loopback OpenVault with no bearer; a fake Bearer 401s. CREW_OPENVAULT=0 in tests so pytest never writes fake keys into the live vault.
