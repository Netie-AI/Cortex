# Crew router harden: OpenVault-armed FreeRoute sources

- **Date:** 2026-09-06
- **Keywords:** crew, router, openvault, freeroute, arm, multi-model, epic-116, grok-bot
- **Main idea:** CREW-ROUTER (#131) was partial because API hosts were configured only from crew env/keys.json. Operator can now arm multiple inference APIs via OpenVault listing + enable/disable. Vault-armed hosts prefer FreeRoute when OV is live. Unarmed pin/turn refuses. `chosen` is stamped on providers/health/message meta. Secrets that vault successfully are not kept in keys.json.
- **Verify:** `python -m pytest tests/test_crew -q` (231 passed locally on this branch)
- **Does not prove:** live `:8020` until founder restart; OpenVault cost/latency bake-off (prefer is measured live via healthz + key listing, not a score). GitHub CI not run from this agent.
- **Cite:** parent Cortex#116; closed CREW-ROUTER #131 / PR #141
