# Crew /build and /scale: seat existing writers, verify by a named test

- **Date:** 2026-09-12
- **Keywords:** crew, scale, build, tickets, seat, verifier, verify-cmd, cap, epic-116, crew-scale-build
- **Main idea:** `/scale` seats existing idle teammates across ready fetched issues and spawns a job-named teammate only while `max_agents_per_space` still leaves a writer + verifier slot; the rest is queued with a visible reason. `/build owner/repo#n | Name | verify cmd` binds one ticket with skill build and a verifier whose criteria name the test command. A verifier that cannot spawn at the cap now says so in the transcript instead of vanishing.
- **Verify:** `python -m pytest tests/test_crew -q` (289 passed locally on this branch: 275 before, 14 new in `tests/test_crew/test_scale.py`). Also `ruff check`, `mypy`, `lint-imports` (3 kept), `python scripts/check_versions.py`, `pytest tests/contract tests/packaging tests/invariants` (151 passed).
- **Does not prove:** live `:8020` HUD until founder restart; that any teammate ran pytest (no shell exec was added, the verifier judges the pasted output and fails closed without it); GitHub CI from this agent.
- **Cite:** parent EPIC-CREW #116. Law: `2026-08-24_overnight-watchdog-tickets.md` ("Scale = seat existing writers, not one agent per issue"). Pack: `CortexOS/crew/skill_packs/build.md` ("Verify default for crew code").

## Expected vs actual

- **Expected** (prompt "Scale and Build tickets or crew then verify Test", read as a Crew feature): the operator fans work across the Tickets HUD without a swarm, every build names its verify command, and verification is honest.
- **Actual before:** `/assign` bound one ticket to one named teammate with no skill pack and no verifier. `spawn_agent(verify=true)` lost its verifier silently at the agent cap (`_spawn_verifier` returned without a message) and the verifier row read `idle` until its first tick. HUD `operator_spawn` with no live run dropped `skills` / `verify` / `verify_criteria`, so the same spawn behaved differently depending on whether a run was live.

## Repro (before this change)

1. `settings.max_agents_per_space = 2`, bind a ticket, let the worker finish with `verify=true`: transcript shows neither a verifier report nor a refusal.
2. `POST /crew/spaces/{id}/agents` with `skills=["build"]` while no run is live: `role_prompt` carries no pack text; the same call during a run does.

## Root-cause class

Silent fallback (verifier skipped at cap with no operator-visible reason) plus a split code path (`_spawn` vs `operator_spawn`) that persisted different fields. Invariants: "Silent fallback is a lie. Degradation has to show in the output." and "A skipped test is a failing test."

## Shape

- No new orchestrator. `/scale` is N x `/build` through the existing `/assign` bind, A2A brief, and one run (`_bind_issue(execute=False)` then `_execute_assigned` per seat).
- Pure planner `CortexOS/crew/scale.py`: cap arithmetic counts verifier rows because they persist as agents (reuse costs 1 slot, spawn costs 2). Verifier rows and Manager are never writers. Goal-mode or waiting teammates are reused only when named.
- `/build` with no args still loads the pack; `build` moved from a skill slug to a desk slash. Assumption stated in the PR: the prompt was read as this feature; slash names are cheap to rename.
- HTTP `POST /crew/tickets/build` (409 SEATED, 400 bad spec) and `POST /crew/tickets/scale` (409 nothing ready). HUD: Build button per ready `owner/repo#n` row, Scale button on the Tickets block. Control stays GET display-only.
