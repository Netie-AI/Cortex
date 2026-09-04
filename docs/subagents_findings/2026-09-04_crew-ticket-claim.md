# Crew HUD Claim is local /assign

- **Date:** 2026-09-04
- **Keywords:** crew, claim, release, hud, assign, seated, issue-162
- **Main idea:** Tickets Claim/Release POSTed routes that 404. Now claim/release are local binds. SEATED is 409. CLAIMS.json untouched. Control still display-only.
- **Verify:** `D:\Cortex\.venv\Scripts\python.exe -m pytest tests/test_crew/test_server.py::test_ticket_claim_is_local_assign_and_refuses_seated tests/test_crew -q`
- **Does not prove:** live `:8020` HUD until founder restart; Control HTML paints assignments (other lane has WIP on NetieControl).
