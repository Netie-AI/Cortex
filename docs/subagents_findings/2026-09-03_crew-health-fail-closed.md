# Crew health and belt fail-closed in 1s

- Date: 2026-09-03
- Keywords: crew, health, belt, fail-closed, PEER_HEALTH_WAIT_S, control-display, r-0011, engine_ok
- Main idea: Engine Crew GET /crew/health and /crew/belt cap Cortex/OpenVault pings at 1s so Control's 1.5s probes get JSON, not a hang. Control slims engine_ok onto the laptop-tools panel. Live :8020 is still the fork until founder rebind.

## Still true

Control stays plane 4. Four 405s. Converse stays :8020. Agents do not restart the hung fork (R-0015).
