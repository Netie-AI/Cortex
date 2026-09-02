---
keywords: [crew, desk, github, GH_WAIT_S, list_org_repos, apiGet, r-0011, control, talk-pool]
main_idea: GET /crew/desk uses catalog estate (no live org list). list_org_repos owners run in parallel at GH_WAIT_S 2s so UI apiGet 2.5s can finish. Control GET / runs talk+health in one pool. Live :8020 still fork.
models: [cursor-grok-4.6]
workflow: 2026-09-03_crew-desk-gh-2s-parallel
reuse: golden_rule
status: verified
cite: distill: docs/subagents_findings/2026-09-03_crew-roles-mdns-metadata.md
repo: Cortex
date: 2026-09-03
---

# Crew desk gh 2s + Control talk pool (2026-09-03)

PREFLIGHT: HIT - INDEX already covers roles unread, ingest-cgnat, health fail-closed, desk parallel peers.

Seated Control GET /v1/contract then pickup/fleet/you/coordinate. Did not POST /v1/run. Did not grow E:\Cortex-crew. Did not restart :8020 or :8040.

## COPY / DISTILL / SKIP / ALREADY

| Item | Verdict | Path |
|---|---|---|
| list_org_repos sequential owners | DISTILL. Two owners stacked GH_WAIT_S. Now parallel. | `github.py` |
| desk estate live gh | DISTILL. desk.snapshot now estate.snapshot(live=False). Live overlay is estate_status. | `desk.py` |
| GH_WAIT_S 4s vs apiGet 2.5s | DISTILL. Cap 2.0 so desk JSON can land. | `github.py` |
| Control GET / talk then health | DISTILL. Talk joins the pool. Hung wakes no longer stack health. | `netie_control/app.py` |
| queue.py wakes.py stall.py | ALREADY. Do not rebuild. | -- |
| Guaca / Beads / LangGraph | SKIP | -- |
| Live :8020 fork, live :8040 7 YOU steps | NEEDS-YOU. YOU step 8. Agents do not restart (R-0015). | -- |

## Tests

- `tests/test_crew/test_connectors_import.py` - org list parallel hang, desk catalog estate
- `tests/test_control_stays_plane_4.py` - talk+health do not stack
