# Netie-Crew host idle none vs unread

Keywords: netie-crew, fleet, r-0011, unread, none, belt, 8022, host-html
Main idea: Seed conveyor GET / names tickets none vs unread and Cortex unread. GET /v1/fleet pointer is on disk; live :8022 still 404s until founder reload. Belt gh+cortex already parallel. converse=false.

## Hunt

| Item | Verdict | Evidence |
|---|---|---|
| GET /v1/fleet pointer vs 404 | ALREADY on disk. Live old process 404. | TestClient 200 empty rows + law. Live `curl :8022/v1/fleet` HTTP 404. Live GET / HTML has no `/v1/fleet`. |
| Belt sequential waits | ALREADY on disk. | `list_open_tickets` ThreadPool per repo; `/v1/belt` pools gh + Cortex. Live belt 4.2s until reload. |
| R-0011 host HTML | SHIPPED | Idle `tickets none` / `handoffs none`. Hung gh `tickets unread`. Cortex fail `Cortex unread`. |
| Second converse on :8022 | SKIP | No composer. `converse=false`. Control still probes :8020. |

## Files

- `E:\Netie-Crew\netie_crew\app.py`
- `E:\Netie-Crew\tests\test_crew_host.py`
- `E:\Netie-Crew\tests\test_crew_stays_plane_4.py`

## Verify

`cd E:\Netie-Crew; $env:PYTHONPATH=E:\Netie-Crew; python -m pytest tests/test_crew_stays_plane_4.py tests/test_crew_host.py -q` -> 18 passed.

Did not kill :8020/:8022/:8023/:8040. Founder reload :8022 to pick up fleet + HTML labels.
