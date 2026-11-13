# DMS iPhone PWA round 2: measured one-hand audit, and two classes it exposed

Date: 2026-11-12. Repo: DMS `apps/ui`, PR #510 (IPHONE-PWA-02).

## Expected vs actual

- Expected: on a 390px phone an answer shows its figure first, and every control is a 44px target reachable with one thumb.
- Actual (measured on the PWA-01 build): 11 tool links at 24px tall wrapped over 5 rows above the figure, so the first screen of an answer held no number. Fixed chrome took 36% of the height. 1 to 13 sub-44px targets per page. Navigation sat top-left. The Ontology map was a 42rem canvas. The derived Space ontology (ONTO-DERIVE-01, on main) had no UI at all.

## Repro

1. DMS API with `DMS_ASK_MODE=demo`, UI dev server.
2. `cd apps/ui && PW_CHROMIUM=<path> npx playwright test e2e/iphone-onehand.spec.ts` on the commit before IPHONE-PWA-02: 7 of 9 fail.

## Root-cause classes

1. **An unlayered element rule silently beats every utility.** `index.css` has `button, input, textarea { font: inherit }` outside a cascade layer. Tailwind 4 utilities live in `@layer utilities`, so a `text-xs` on a `<button>` does nothing: every button renders at the inherited 16px. That is why "text link" buttons measured 24px tall. Not fixed here (it changes every desktop button); the tab bar sets its label size on a `<span>` instead.
2. **A hook that sizes an element must depend on everything that changes its content.** The phone composer auto-sizes from `scrollHeight`. The placeholder swaps to a longer text while an ask runs, Chromium counts a wrapped placeholder in `scrollHeight`, and the effect only depended on the draft, so the field stayed 66px after every send. The first test did not send a question, so it passed. Proved by reverting the dependency: `Expected <= 48, Received 66`.
3. **A superseded fetch must not write state.** `SpacesPage` set an error from an aborted request; under StrictMode the "aborted" text replaced the Spaces list.

## Invariants that applied

- Prove a gate can fail before trusting it green (class 2; and the ontology agent mutation-checked 5 of its 6 tests).
- Assert the artifact the user receives (rendered boxes and text at 390x844, not class strings).
- Adversary is not the verifier (the audit agent measured a frozen build; a different agent built; the main session reviewed the diff and reran every test).

## Subagent shape (and why)

Four agents, all Sonnet (DMS CLAUDE.md pins fan-out to Sonnet), isolated contexts, the main session planned, built the chat screen and judged.

| # | Lens | Mode | Why isolated |
|---|------|------|--------------|
| 1 | Mobile one-hand design research (web) | read-only | Keeps page dumps out of the main context. Result: 25 rules, only WCAG and NN/g actually fetched, the rest tagged memory. |
| 2 | Measured audit of the frozen build at 390px | read-only, Playwright | Not anchored on what the builder thought it had fixed. Result: 20 ranked defects with sizes and file:line. |
| 3 | Ontology / accuracy / open-lane map of both repos | read-only | Wide read across two repos. Result: ONTO-DERIVE-01 is on main with no UI; live scoring is blocked on OpenVault keys and Cortex. |
| 4 | Build the phone ontology screens | write, disjoint file list | Ran in parallel with the main session's chat-screen work. Ownership by explicit file list, no git. |

Research before audit was not needed: 1 to 3 ran in parallel. What did need the main session: ranking the audit, the answer-layout design, and the review of agent 4's diff against the real API shape.

## Not verified

Chromium emulation only. No real iPhone. Ontology screens ran against stubbed responses in the backend's shape, not a live derived ontology. No live Cortex ask, so nothing here measures answer accuracy.
