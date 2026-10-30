# TASK.md

Live list as of 2026-10-30 MYT. Main is `2c7e7e7` (#453 merged, contract 1.10.0).
Direction and baseline: `STATUS.md`. AI-first law: `CLAUDE.md` section 9.
`docs/archive/task.md` is an old phase list and does not bind.

Merge one at a time. A new commit on a head means that head is reviewed again.
Merge gate (founder ruling 2026-10-29, #436): CI 5/5 on the exact head, squash
with the expected head SHA.

## Open chain

1. **#321** VERIFIED-QUERY. Steward-confirmed examples, contract 1.11.0. Needs
   founder sign-off on the new `.importlinter` contract `verified-query-library`.
2. **#445** LANES-V1-01 (LANE-ROUTER-01 PR-L1). Frozen lane labels, corpus and
   checkers. Waits on the blind relabel and founder adjudication.
3. **#447** LANE-ROUTER-01 PR-L2. `CORTEX_LANE_ROUTER`, default off. Stacked on
   #445.
4. **#450** SUPPLY-CHAIN-LEFTOVERS. Closes #375, #377 and #383.

## Follow-ups

- **DuckDB LIMIT.** A LEFT JOIN whose ON carries a constant filter the optimizer
  proves empty drops the `LIMIT`, so a served answer can exceed its `LIMIT` and
  the 1000-row cap. A fix is in progress.
- **PICK-READING-01 (#453).** Phase 0: BIRD split A with
  `CORTEX_PLAN_SQL_READINGS` on, before the flag goes on anywhere. DMS consumes
  1.10 (steps in `CHANGELOG_DMS.md`).
- **#413.** Measure the 52-question set against real gold before
  `CORTEX_VERIFIED_QUERY` goes on anywhere.
- **Dependencies.** #393 (multidict 7) needs a coordinated aiohttp, multidict
  and yarl bump. Dependabot pip should not edit the image locks.
