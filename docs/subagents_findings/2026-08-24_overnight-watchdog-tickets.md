---
keywords: [overnight, watchdog, night_watch, tickets, verify, paging-file, ram, schtask]
main_idea: Overnight keep-alive failed because schtask ran estate-watchdog only. Cursor night_watch loop ended at 09:00 so Crew/OpenVault died. Fix is estate-watchdog calling night_watch -SkipEstate. Scale is seat existing writers, not one agent per issue. Low RAM/paging file must skip extra gh/gate or the tick dies.
models: [cursor-grok-4.6]
workflow: 2026-08-24_overnight-watchdog-tickets
reuse: golden_rule
status: verified
cite: distill: D:\Cortex-crew\docs\NIGHT_WAKE.md
repo: Cortex-crew
date: 2026-08-24
---

# Overnight keep-alive + tickets + watchdog

## Main idea

The 15-min scheduled task launched estate-watchdog (Plane + Grok). Crew :8020 and OpenVault :5000 were only restarted by a Cursor `night_watch` loop that stopped after 09:00. Computer control must stay off on that restart. Ticket Runner seats existing writers; verify is a different run. When the paging file is exhausted, skip gh/gate for that tick and still keep processes alive.

## Golden rule

> Estate launches Crew/OpenVault via `night_watch -SkipEstate`. Ticket Runner seats, it does not swarm. Verify is R-0003 on another run. Below ~512MB free RAM, keep-alive only -- do not spawn `gh` or `estate_gate.py`.

## Verify

```
GET http://127.0.0.1:8020/crew/health
GET http://127.0.0.1:8010/api/engine/activity
GET http://127.0.0.1:8010/api/workflows/tasks
GET http://127.0.0.1:5000/api/healthz
GET http://localhost:8099/api/instances/
schtasks /Query /TN NetieEstate24x7
Get-Content D:\Cortex-crew\docs\NIGHT_WAKE.md -Tail 5
```
