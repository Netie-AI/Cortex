# Crew /fetch and /assign (local bind)

- **Date:** 2026-09-04
- **Keywords:** crew, assign, fetch, belt, seated, f-0030, epic-116, issue-160
- **Main idea:** Crew fetches open GitHub issues and binds an unseated ticket to a teammate goal. Control still display-only. CLAIMS seating stays Ticket Runner. No GitHub assignee write.
- **Verify:** `cd D:\Cortex-wt\crew-facts` then `D:\Cortex\.venv\Scripts\python.exe -m pytest tests/test_crew/test_commands.py tests/test_crew/test_wakes.py tests/test_crew/test_connectors_import.py tests/test_crew/test_server.py -q`
- **Does not prove:** live `:8020` has `/assign` until founder restart; CLAIMS-seated tickets were closed (they must not be).

## Golden rule

Control GET `/v1/belt` may show `assignments`. POST assign on Control stays not-200. `/assign` refuses SEATED. `/done` HITL still closes only unseated issues and drops the local bind.
