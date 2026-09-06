# Crew belt claim/release is a lease SoT

- **Date:** 2026-09-06
- **Keywords:** crew, belt, claim, release, lease, ttl, 409, epic-116, crew-belt-claim
- **Main idea:** Chrome Claim/Release POSTs `/crew/tickets/{id}/claim|release` on a Crew ticket-lease ledger (worker + TTL). 409 if held elsewhere or SEATED. Release is holder-only (403 else; 409 if not held). GET `/crew/belt` stays Control-display compatible. Control does not lease. CLAIMS.json untouched.
- **Verify:** `python -m pytest tests/test_crew -q` (208 passed locally on this branch)
- **Does not prove:** live `:8020` HUD until founder restart; Control HTML (display-only; other lane). GitHub CI not run from this agent.
