# Crew mailbox cursors + stall on engine tree

| Date | Topic | Keywords | Main idea |
|------|-------|----------|-----------|
| 2026-09-03 | crew-mailbox-stall-engine | crew, a2a_cursors, stall, ship-gate, event-wake | Engine tree now persists per-agent consume cursors, replays inbox after restart, seq-age cuts teammates only, ship_gate fires event wakes. Do not rebuild. Live :8020 is still the fork. |

Verify: `python -m pytest tests/test_crew/test_store.py tests/test_crew/test_a2a.py tests/test_crew/test_wakes.py -q` from `E:\Cortex`.
