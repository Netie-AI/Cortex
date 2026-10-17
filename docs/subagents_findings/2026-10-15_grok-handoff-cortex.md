# Handoff to Grok: Cortex after the 2026-10-09 brief check (2026-10-15)

Claude stopped its build workflow on the founder's instruction. This note lists what Claude pushed, what is still open, and the next steps toward the AI data platform. Every SHA below is on GitHub.

## Founder rulings in force
1. **Writer:** Claude may write and review Cortex code (a waiver of the Grok-only rule for that lane). AGENTS.md rule 1 still needs an AGREE from a different model family, so Grok can AGREE on Claude-written commits.
2. **Contract:** additions take a minor bump. #351 publishes 1.6.0, and published 1.5.0 is byte-identical to main.
3. **AI path first:** `/v1/insights`, the path DMS's live 52 uses. The #329 ladder on `/v1/contract/ask` comes after.
4. **PII:** as CLAUDE.md §9 says. No PII abstain, and no masking change.
5. **Branches:** #235 was reset to main. Fixes on cursor/* PR branches are merge or fast-forward commits only.

## Where things are
| PR | Head | What Claude pushed | Review state | Verified locally |
|---|---|---|---|---|
| #235 (claude/cortex-scale-agi-state-fkg2b6) | see PR | BIRD P1/P2 finding (docs). R1: MANIFEST-LATERAL-01, GRANT-FOLD-01, DB-ERROR-TEXT-CX-01 (2 commits) | MANIFEST: adversarial AGREE plus different-model AGREE. GRANT-FOLD: adversarial AGREE; the second reviewer did not run. DB-ERROR: the first adversarial DISAGREE (raw error text on the submit runtime path) was fixed by the second commit, and the re-review did not run | Execution, plan_sql and contract tests 501/501. Full suite: see the PR comment. ruff clean. lint-imports 5 kept |
| #351 (cursor/schema-context-01-5b41) | 18e30cc | SC-351-FINISH: contract 1.6.0 with 1.5.0 restored; an undeclared column or unbounded listing becomes a named retry, not a terminal refusal; engine-pack trials refused when schema_context is sent | Not reviewed | contract, packaging, schema_context, cross-catalog and test_crew: 698 passed. check_versions and check_contract_compat OK. export_openapi --check could not run here (no `.[full]`), so CI confirms drift |
| #345 (cursor/cert-oracle-split-7594) | ebab13e | Merged main (.importlinter, with INVARIANT-CHANGE:); fixture uses a key header after #366; the ladder joins serve-never-reads-oracle; the climb oracle no longer copies held-out data, and a run with no gold says NO_GOLD | Not reviewed | cert-oracle, climb, plan_sql and contract tests: 210 passed. lint-imports 6 kept |

## 1. Finish the stopped workflow (in this order)
1. **Review what was pushed.**
   - Different-family AGREE and PR Bot CLEAR on #351 at 18e30cc, plus a fresh Security review: the last Security NO was on an older head.
   - Review #345 at ebab13e.
   - On #235, a second reviewer for GRANT-FOLD-01 and a re-review of DB-ERROR-TEXT-CX-01.
   - Any red CI on these heads is the first job.
2. **GUARDRAIL-TABLEFN-01 needs a rework. It was not pushed.**
   - The goal: `sql_guardrail.validate_sql` refuses functions used as relations, and checks the real source column behind an alias.
   - Both reviewers rejected the first version:
     - it refused ordinary queries (GROUP BY, QUALIFY and WHERE by an alias), which blocks legitimate work;
     - it still let some alias forms rename a column that is not allowed.
   - Rebuild it from the DuckDB 1.5.5 binding rules, with positive controls for every common alias use. Claude can share the detailed findings privately.
3. **Build the three unbuilt R1 items.**
   - AI-SQL-READS-TABLE-01: model SQL is served only if its output derives from a granted base table.
   - L2-SHADOW-GRANT-01: no shadow execution without a verified grant.
   - NAME-ECHO-CX-01: one response-boundary scrub of ungranted names on `/v1/contract/*`. Then the same on `/v1/insights` and the MCP answer tool.
4. **#351 follow-ups found during the build.**
   - With schema_context, the response `ontology` field still carries the engine-pack ranking.
   - About six mypy errors already exist in cot_climb.py and schema_context.py on the PR's base.
   - Keep one schema_context parser when #386 is rebuilt.
5. **#345 follow-up.** The climb has no gold now. Exact-SQL climbing needs a new development split, written independently of the held-out, golden and paraphrase files. Rule on whether to show NO_GOLD or 0.00%.

## 2. Next stops toward the AI data platform
1. **Make the route measurable (decision 4).**
   - Lane stamps on every answer: model SQL vs rule lanes, model abstain vs rule abstain.
   - Fix the `/v1/insights` stamp inversions: l2_serve model SQL is stamped as rule, and certified stored SQL is stamped as ontology_plan.
   - Align the DMS #432 grader with those stamps.
   - It is a contract minor after #351 (1.7.0, or folded into 1.6.0 if it lands before 1.6.0 is consumed).
2. **Meaning as data on `/v1/insights` (decisions 3 and 11).**
   - VALUE-DOMAIN-01 (#387): one declared txn_type domain (canonical `outbound`), mapped at every lake build. Then #385.
   - METRIC-SEMANTICS-01, which needs a ticket: declared measures (expression, required filter) rendered into the SQL prompt as trusted data, plus a structural check whose violations go back to the model as a retry.
   - Before #387 lands, decide how gold SQL that still says 'OUT' runs on a mapped lake.
3. **One ladder (decisions 1 and 5).**
   - Bring the `/v1/insights` climb and the #329 ladder onto one path: retry, then a stronger OpenVault tier, then plan-then-solve, then reconfirm.
   - Rebuild #386 on main and close the #388 items before `CORTEX_PLAN_SQL=1`.
   - No AI-call error may fall to a rule answer (`/dms/query` and the Insights bridge still do).
4. **Recheck and safety at the boundary (decision 8).**
   - An `Answer.recheck` payload: executed SQL, steps, check verdict, capped row sample.
   - The drillthrough 500 on GROUP BY answers.
   - Name echo on every exit.
5. **Retire rules one lane at a time, measured.**
   - Once the stamps work and the AI serves in prod, land #313 and then each lane.
   - The gold-leak lane goes now: DMS #435 plus #345.
   - Report each PR's route shift from Check's live 52 (2 copy-only runs).
   - Any WRONG=0 claim needs a blind held-out pack of at least 100 answered questions (rule of three).
   - The external benchmark stays BIRD Mini-Dev: 50.4% on the pure-AI harness, see `2026-10-09_bird-pure-l2.md`.

## Cleanup waiting on the founder's go (closes are not done)
- Recommended closes: #338 (rewrite #306 first), #316, #318 (port their checks into the ladder first), #330, #281, #239, #382, plus the dead Dependabot bumps #219, #221 and #378 to #381.
- #218 should be replaced with next >= 16.3.8.
- Each close's "what it loses" list is in the private research report.

## Verified vs assumed
- **Verified:** the SHAs, the test counts and the review verdicts above, from this session's runs.
- **Assumed:**
  - that CI on the pushed heads matches the local runs;
  - that the 1.6.0 spec matches export_openapi output (the writer reports `--check` OK, and CI confirms it);
  - nothing here is measured on the live 52.
