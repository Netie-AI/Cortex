# start_crew.ps1 refuses double-bind on hung :8020

- Date: 2026-09-03
- Keywords: start_crew, PortHeld, R-0015, YOU step 8, double-bind
- Main idea: YOU step 8 script start_crew.ps1 exits 1 when :8020 is held and /crew/health unread. Sets PYTHONPATH. Does not start a second Crew. Agents still must not kill the hung fork.

## Still true

Live :8020 is the Cortex-crew fork. Control four 405s. Converse stays :8020.
