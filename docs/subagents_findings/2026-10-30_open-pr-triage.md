# Open PR triage, 2026-10-30

Main at triage: `aae3cbc` (#444). Contract on main: 1.9.0 (#386). 1.8.0 was never
published. No open PR claims 1.10.0 (#446 does not exist yet).

Scope: #321, #317, #333, #334, #337, #359, #315, #313. Decisions are (a) drive to
mergeable on the PR's own head with a merge commit, or (b) recommend closing.
This triage closes and merges nothing.

## Decisions

| PR | Decision | Reason | Evidence | Head after push | CI state |
|---|---|---|---|---|---|
| #321 VERIFIED-QUERY | (a) drive | Verified-example context for the SQL generator is section 9's own design. Six DISAGREE rounds each closed; the last fix `c983d4d` has no review yet. Only blocker was staleness and the contract number. | Merged main (merge commit). 4 conflicts resolved. Contract renumbered 1.8.0 -> **1.10.0** (next free minor; 1.8 slot was never published). Branch-only `openapi-1.8.0.json` dropped. 18 published specs byte-identical to main. `check_contract_compat`: 1.9 -> 1.10 is 2 additive, 0 breaking. | `6daf2e5` | pushed; CI running. Local: 4429 passed, 12 skipped, 4 xfailed; all static checks OK |
| #337 #235 slice 1 auth port | (a) drive | Still needed: on main, `/api/connectors/*` (incl. `/dispatch`, `/computer-control/invoke`) and `/api/apps` carry no role gate. #235 merged as R1-R3 without TRUST-01. | Merged main (merge commit). 5 conflicts resolved. 3 tests broke on the merge and were fixed without loosening (see notes). Static checks green. | `101da72` | 4/5 green (base-install, protected-paths, rls-proof, secrets-scan); lint-type-test running. Local: 4343 passed, 12 skipped, 4 xfailed |
| #313 C7-06 | (b) close, superseded | #424 already does what #313 was for: a model chooses the governed metric, and `route_to_metric` serves only a metric's declared question (exactness guard). #313's held-out gate was never met (1 correct / 27 abstain / 0 wrong). #424 measured 0 wrong in 398 asks. | `git merge-tree` vs main: 20 conflicts, `answer_engine.py` and `query_service.py` included. Climb license was keyed to base `c7469da4`, which main left long ago. | `93e5038` (unchanged) | not re-run |
| #315 docs two-tier gate | (b) close, superseded | #339 (`99eea21`) shipped the two-tier gate as code (`decide()` in `scripts/auto_merge_if_perfect.py`) plus AGENTS.md binding rules 1-2 and the comment templates. #315's text says "Until #339 lands" and has an open different-family DISAGREE (5 blockers) at `430eacc`. | AGENTS.md on main already states tier:fast / tier:full, exact-head CLEAR and AGREE, short-SHA rule. Conflict in STATUS.md. | `de1b449` (unchanged) | not re-run |
| #333 CLARIFY | (b) close, violates section 9 | Ambiguity is detected by word rules on the question: `_GENERIC` stop-word list and `_VAGUE_WINDOW` / `_EXPLICIT_WINDOW` regexes (`recently`, `lately`, `these days`...) in `CortexOS/clarify/core.py`. Section 9: word rules stay out of the answer path. The 2026-10-29 "split readings" ruling now covers this need through the model ladder's reconfirm. | Also rewrites main's frozen `contract/openapi-1.5.0.json` (claims 1.5.0; main published 1.5.0 via #329). 56 commits behind. | `cc652fc` (unchanged) | not re-run |
| #334 SUGGEST | (b) close, violates section 9 | Follow-up questions are written by fixed English templates (`"Show the {table} rows where {dim} is '{value}'."`, `"Compare ... versus ..."`, `"How has ... changed over ..."`); the model only rephrases, behind a flag. Served/abstain is read from token sets (`_ANSWERED_BADGES`, `_CLARIFY_TOKENS`) that predate main's badges. Section 9 reconfirm already suggests the closest answerable question through the ladder. Rebuild #308 model-first. | Rewrites frozen `openapi-1.5.0.json`; adds `.importlinter` contract that main never got. 56 behind, 9 conflicts. | `111c282` (unchanged) | not re-run |
| #317 C-LOOP-C | (b) close, obsolete | Rebuild on main rather than merge. It rewrites main's frozen `openapi-1.5.0.json` (CLAUDE.md section 3), and its flag-off golden fixture pins an Oct-6 main that no longer exists. The insight is `template.v1` prose, so a rebuild should route answer text through FreeRoute. #305 stays open. | 56 behind, 8 conflicts, all in contract files and `contract_routes.py`. | `65f11c2` (unchanged) | not re-run |
| #359 STEP-TRACE-01 | (b) close, obsolete | It stacks #351's pre-merge commits. #351 landed differently (`657adf7`, then the #404 fix `78545d7`), so a merge would bring superseded `schema_context.py` / `insights/routes.py` code back. It also rewrites frozen `openapi-1.5.0.json`. PR Bot HOLD 6068685229 asked for a rebuild after #351 merged; that rebuild never happened. #353 stays open; rebuild the step-trace delta alone from main. | 55 behind, 12 conflicts incl. `schema_context.py`, `cot_climb.py`, contract files. | `d094669` (unchanged) | not re-run |

## Notes on the two driven PRs

### #321

- Conflict in `CortexOS/api/contract_routes.py`: kept both sides. Main's
  `FreeRouteStrictError` handling around `try_plan_sql` stays as is, and the
  VERIFIED-QUERY bind / take / stamp around `answer_engine` is unchanged.
- Contract: `version.py` and `pyproject.toml` both at 1.10.0. `compat.yaml` has 1.9
  as `supported` and 1.10 as `current`. `answer.py` and the VQ tests now say 1.10.0. The
  flag-off test now pins the 1.9.0 Answer, the previous published spec, where it pinned 1.7.0.
  The requirement is the same: flag off, the wire is the last published Answer.
- Three semantic breaks surfaced in the full suite after the merge (8 failures)
  and are fixed:
  1. **Copy rule vs #435 page tiebreak.** Main now appends the table key to the
     ORDER BY of a page its LIMIT cuts. A verbatim copy of an example then no
     longer matched the example's copy key, so `example_echo` passed it and only
     the backstop `result_echo` refused it, under the wrong reason. Still an
     abstain (WRONG=0 held), but the named rule was dead. Fix:
     `_without_page_tiebreak()` drops only a suffix `apply_page_tiebreak` itself
     would append, and the copy rule compares that. `result_echo` still runs the
     SQL that executes. Seen failing first: both copy must-fails and the
     `example_echo` guard proof were red before the fix.
  2. **#424 declared wording.** The flag-off byte-identity test served
     "what is our total revenue" through the old cascade. #424 serves only a
     declared wording, so the test now asks "what was total revenue" (declared
     for `revenue_total`). It still requires a served answer in the comparison.
  3. **#424 unarmed reconfirm.** An unarmed ask now carries main's metric
     reconfirm. The wire-shape test asserted `reconfirm is None`. It now asserts
     the reconfirm has main's shape. The lane-specific checks are unchanged.
  - Expected rows in the VQ tests are read in the serve path's total order
    (`apply_page_tiebreak` with the real page count). 12 inventory rows tie at
    500 kg, so a bare `LIMIT 5` read was nondeterministic.
- If a 1.10.0 PR (e.g. pick-your-reading) merges first, #321 moves to 1.11.0 the same way.
- The tip commit carries `INVARIANT-CHANGE:` because the PR diff still touches
  `.importlinter` (protected-paths reads only the tip body).
- Still owed before merge (unchanged by this triage): CI 5/5 on the new head,
  PR Bot CLEAR, a different-family AGREE on the exact head (PR Bot rule
  6253329606: one grok-4.6 high review), Check, Lead Formal. Last HOLD on the
  thread is 6246436609 @ `c4f6a1b`; `c983d4d` answered it and was never reviewed.

### #337

- `packs/dms/__init__.py`: registers main's metric choice and the request authorizer.
- `pyproject.toml` `addopts`: both plugins,
  `-p tests.api_key_isolation -p tests.dms.metric_choice_plugin` (main added the
  second in #424).
- Merge breaks fixed:
  1. `test_key_isolation_plugin_scope.py::test_plugin_is_the_only_addopts_plugin`
     pinned the old single-plugin line. It now pins the exact two-plugin line, so
     any third plugin still fails it.
  2. `test_fail_closed.py`: main renamed `api_auth._caller_from_openvault` to
     `verify_openvault_key` (#327). Same fake, same assertions, new name.
  3. `tests/packaging/test_profile_routes.py` (from main) sent `dms-demo-*` keys.
     With the suite-wide test keys set, `/run` answered 401 before the 501 it
     asserts. Swapped to `pytest-admin-key` / `pytest-steward-key`, the same
     swap the PR already made in 5 other tests. Same 501 assertion.
- The three test-file conflicts took the PR side (explicit per-test admin key and
  `DMS_AUTH_DISABLED` deleted).
- The full suite then showed 74 failures with one cause. The PR's suite plugin
  sets `DMS_API_KEYS` for every test, and 20 test files that main added since
  then send the published `dms-demo-*` keys, so they got 401. Each file's client
  now sends the `pytest-*` key of the same role, the swap the PR already made in
  5 files. No assertion changed. Not a product change: main already refuses
  demo keys on `api_auth` routes once `DMS_API_KEYS` is set; the suite now
  always sets it. PR Bot item 3 (which DMS callers still send demo keys) stays
  open.
- Still owed (PR Bot HOLD 6009656444): body rewrite to match the diff, DMS caller
  route list, Lead OK on the `addopts` plugin vs the #215 conftest freeze, then CI
  5/5, CLEAR, different-family AGREE, Check, Lead Formal.

## Verified vs assumed

- Verified (commands run on this machine): merge-tree conflict counts, frozen-spec
  diffs, the word-rule and template lines quoted above, all static checks and the
  full pytest runs listed in the table.
- Assumed: GitHub CI results for the new heads (see CI column; pending at writing).
  That #305, #307, #308, #353 still want the features (all four issues are OPEN).

## Finding

- Expected: old open PRs either merge clean on main or get closed.
- Actual: four PRs (#317 #333 #334 #359) each carry a private `openapi-1.5.0.json`
  that would overwrite main's frozen 1.5.0. Two (#333 #334) put word rules or
  templates where section 9 wants the model.
- Repro: `git diff --quiet origin/main origin/<head> -- contract/openapi-1.5.0.json`
  exits 1 for each.
- Root-cause class: long-lived branches that claim a contract number at branch
  time, not merge time.
- Invariant: "Never hand-author generated artifacts" / CLAUDE.md section 3 (frozen
  specs), and section 9 for #333 #334.

Side note: `.cursor/rules/merge-perfect.mdc` on main points at a "§ Merge tiers
(PR #315)" section that does not exist. If #315 closes, that pointer should move
to AGENTS.md binding rules 1-2.
