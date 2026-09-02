# Netie-Crew belt fail-closes hung gh

Keywords: netie-crew, belt, gh, timeout, GH_WAIT_S, r-0011, 8022
Main idea: Seed :8022 list_open_tickets caps gh at 1.5s per repo in parallel. Hung gh is unreachable, not a hang. Control still probes :8020.

Live `:8022` may lag until founder reload. Do not restart `:8020` or `:8040`.
