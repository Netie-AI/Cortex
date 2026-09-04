# Crew tickets GET includes fetched GitHub issues

- **Date:** 2026-09-04
- **Keywords:** crew, fetch, tickets, hud, issues, claim, issue-164
- **Main idea:** Tickets HUD was CLAIMS-only so Claim could not bind ecosystem issues /fetch already listed. GET /crew/tickets now adds `issues` (dedup CLAIMS specs). CREW_LIVE_PROBES=0 stays empty.
- **Verify:** `D:\Cortex\.venv\Scripts\python.exe -m pytest tests/test_crew/test_server.py::test_tickets_lists_fetched_github_issues_minus_claims tests/test_crew -q`
- **Does not prove:** live `:8020` HUD until founder restart; Control HTML (WIP on another lane).
