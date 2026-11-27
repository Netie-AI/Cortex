# DMS iPhone PWA round 3: what four independent checks found in work that had passed its own tests

Date: 2026-11-12. Repo: DMS `apps/ui`, PR #510 (IPHONE-PWA-03). Stack: DMS API in demo ask mode, Chromium emulation, and for the first time a local Cortex (core profile, dev mode) on :8010.

## Expected vs actual

Rounds 1 and 2 shipped with every test green and the claim "desktop is unchanged". Round 3 ran two build-then-verify chains and four read-only checks. Both verifiers returned FAIL and every check found real defects.

| Claim before | What the check measured |
|---|---|
| Desktop unchanged | 7 visible desktop changes at 1280px against a build of `main` (scope chip width, a new card on Spaces, a different Ontology error state, tab spacing, row heights, pager height, chart row font) |
| Offline shell caches only the app shell and hashed assets | A missing `/assets/*.js` is answered by the SPA fallback with 200 `text/html`, and the cache-first handler stored that HTML under the script URL, for good. Nothing was ever pruned. |
| Answer first on a phone | After every ask the thread scrolled to the END of the answer, so the figure was above the screen (text top at -972px on a 375x667 phone) |
| All CI-relevant tests pass | CI failed: `tests/test_studio_mobile_layout.py` (pytest) pins the same layout strings as the vitest file that had been updated |
| Tables and charts done, tests green | Sticky column with no width cap covered the whole table on a long value; desktop pager and ranked chart changed; 11 mutations left the tests green |

## Repro

- Desktop: build `origin/main`'s `apps/ui`, serve both builds, compare bounding boxes and pixels per route at 1280x800. `e2e/desktop-unchanged.spec.ts` now pins each case.
- Cache: on the old build, in a page controlled by the worker, `fetch('/assets/index-GONE.js')` returns 200 `text/html` and the entry appears in cache `dms-shell-v1`. On the fixed build it does not.
- Scroll: ask any question at 390x844; `answer-text` top is above the scroller. `e2e/iphone-onehand.spec.ts` "a finished answer is shown from its top".
- CI: `python -m pytest tests/test_studio_mobile_layout.py` on `f12b58f`: 2 failed.

## Root-cause classes

1. **Scope by convention leaks.** "Phone only" was enforced by remembering to write `max-lg:`. Nothing compared desktop against the base, so an unprefixed `min-h-11`, a wrapper div and a new component each changed desktop unnoticed. Class: a negative claim ("X did not change") needs its own gate against the base, not the absence of a failing test.
2. **Caching by status code alone.** An origin that answers every unknown path with the app shell and 200 makes "cache if ok" wrong. The response type has to be checked, a redirect excluded, and old entries pruned.
3. **One requirement pinned in two suites.** The vitest pins were updated, the pytest mirror was not, and the Python suite was skipped on the reasoning "no Python file changed". Tests that read non-Python sources break that reasoning. Search the whole test tree for a pin before changing it.
4. **Assertions that cannot fail.** Playwright `toBeVisible` passes for an `opacity: 0` element. `elementFromPoint` returns a transparent cell. `Math.max(width, height) >= 44` passes a 24px-tall full-width row. A poll for `top <= 16` passes when the element is far above the screen. Each was found by a mutation that left the tests green.
5. **A test that reads mid-animation.** Two tests read boxes one call at a time while the thread smooth-scrolled, so they passed or failed by timing. Measure in one frame, or wait until `scrollTop` stops changing.
6. **A verifier is also a writer.** A mutation-testing agent left one mutation in the working tree. A commit taken during a verifier's run would ship a deliberately broken file.
7. **A font rule outside the cascade layers** (from round 2) bit again: a `text-xs` on a `<button>` did nothing, so chart rows rendered at 16px on desktop.

## Cross-repo facts found on the way (verified, not fixed)

- DMS `apps/api/dms_api/routes/ontology.py:43` proxies to Cortex `GET /dms/ontology`. This Cortex answers 404, and `tests/test_api_security/test_dms_demo_key_callers.py:54` asserts that 404. The DMS Ontology page therefore always shows "unavailable" against this engine.
- Cortex core installs and runs in dev mode with no keys. With it up, DMS's compliance gate answers and `apps/ui/e2e/library-receipt.spec.ts` passes 2 of 2 (it had never run past its ingest here). That spec had two ambiguous `getByLabel("Space")` locators, present on `main`.
- Live ask mode with no OpenVault returns an honest ABSTAIN (`insights_bearer_missing`, `values: []`, `demo_fallback_used: false`). No figure is invented.
- A derived Space ontology cannot be produced here: derive accepts only a SQL Server, MySQL or PostgreSQL source and none is reachable.

## Invariants that applied

- Adversary is not the verifier: every defect above was found by an agent that did not write the code.
- Prove a gate can fail before trusting it green: 17 + 11 mutations.
- Assert the artifact the user receives: the cache check ran on a production build, not on the source.
- Uncommitted is not done, and committed is not verified: the tables and charts checkpoint said so in its commit message.

## Agent shape (and why)

One workflow, 8 agents, all Sonnet (DMS fan-out law), isolated contexts, shared working tree with explicit file ownership and no git. About 1.02M subagent tokens, 38 minutes. The container allowed 2 agents at a time (cap is CPUs minus 2), so "parallel" ran as a queue: builders first, then the checks, then the verifiers.

| Stage | Agents | Why isolated |
|---|---|---|
| Build | tables, charts (disjoint files) | Parallel with the lead's own edits |
| Verify | one per build, told to refute and to mutate | Not anchored on the builder's report |
| Check | multi-phone audit, desktop comparison against `main`, diff review, live Cortex bring-up | Each needs a long measurement or read that would flood the lead's context |

A ninth agent then fixed the table and chart defects from the two FAIL verdicts while the lead fixed the rest. What needed the lead: ranking 60 findings, deciding what stays phone-only, and the cache worker.

## Not verified

No real iPhone. No live model answer (needs OpenVault and a key). The cache check is a script run by hand on a production build, not yet a test.
