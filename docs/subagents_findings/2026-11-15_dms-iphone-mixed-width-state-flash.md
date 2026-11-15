# DMS phone: state derived in one place and read in another showed one commit of the wrong layout when the window crossed lg

Date: 2026-11-15. Repo: DMS (`Netie-AI/dms`), PR #510, ticket #512 (EPIC-008). No answer-path code involved.

## Expected vs actual

Expected: narrowing the window from lg (1280 to 800, 1024 to 390) with a draft in the composer leaves the composer focused and nothing modal open. A docked Sources panel does not become a modal sheet uninvited.

Actual (round 6 and 7, observed in Chromium): the Sources sheet (and, in Operate mode, the nav) rendered as `role=dialog` with `inert` on header, main and tab bar for one commit, moved focus into itself, then reverted. The composer lost focus for a frame. A reseed-during-render fix in `AppProvider` cut the symptom but a per-frame sampler on the round-8 checker still counted 62 to 73 composer-unfocused frames and 310 to 511 DOM mutations over 150 fast crossings.

## Repro

`apps/ui`, any keyless stack: load 1280x800, type in the composer, `page.setViewportSize` to 800x600 and back in a loop with no waits, sample `requestAnimationFrame` plus a `MutationObserver` on `role`, `aria-modal`, `inert`. Against `72390dd` the flash shows. Against `89be214` it does not (0 frames, 0 mutations, 0 blurs, 150 loops, both themes).

## Cause (inferred, not instrumented)

Child components read the width through their own `matchMedia` listener (`useIsLg()`), the provider kept a boolean computed from its own listener. Chromium runs a microtask checkpoint between listeners of one change event, so React can commit after a child's listener and before the provider's. That commit pairs the new width with the old provider state.

## Root-cause class

**Derived state stored in one subscriber and read in another subscriber of the same external signal.** Two values that must agree (width and "open") were kept in two places that update on different ticks. A setState-during-render reseed moves the disagreement, it does not remove it. The fix is structural: store the independent facts separately (`dockOpen`, `sheetOpen`, `navLgCollapsed`, `navDrawerOpen`) and let every reader derive the effective value from its own read of the signal, so no commit can mix them.

Sibling to look for: any provider that computes a boolean from a media query, online status, or visibility and exposes it next to a component that reads the same signal itself.

## Invariants that apply

- Assert the artifact the user receives: the earlier test asserted the end state only and was green while the composer blurred for a frame. The new spec samples every frame.
- Prove a gate can fail: the new sampler is red against the previous head (7 of 18 tests) and still red with its frame channels zeroed, because the mutation and blur channels catch it.
- Fix the root-cause class, not the symptom: the reseed was the symptom fix.
- Separate verified fact from assumption: the listener-order explanation is inferred from the log order and from a provider-computed mutation going red, not from instrumenting the listeners.

## Also recurred

A second change broke CI because `tests/test_studio_mobile_layout.py` mirrors the vitest pin of `AppContext.tsx` and was not run after the change (same class as `2026-11-12_dms-iphone-round3-checks`, "pytest mirrors vitest pins"). Fixed in `7f8d97b`. The builder ran vitest and Playwright only; the brief's gate list now needs the Python layout files after any `src` change.

## Not verified

Real iOS Safari, VoiceOver, soft-keyboard behaviour on iPadOS (a focus blur there may dismiss the keyboard), a true single-commit transient (`setViewportSize` delivers each resize as its own event).

## Agent shape

Orchestrator on the session model. Fan-out on Sonnet, isolated context with written briefs: one builder (single writer, no git), one checker that did not write the change (hash-verified restores, 27 mutations, 3 sampler mutations). Opus was not needed for any step.
