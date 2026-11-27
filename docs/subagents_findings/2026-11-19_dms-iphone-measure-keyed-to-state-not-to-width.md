# DMS phone: sizes measured when state changed, not when the width did; a chart overlay sized to the visible box, not to its content

Date: 2026-11-19. Repo: DMS (`Netie-AI/dms`), PR #510, ticket #512 (EPIC-008). No answer-path code involved.

## Expected vs actual

Expected: (1) the phone composer is exactly as tall as its text needs after a rotation; (2) a phone line chart draws one polyline through its own dots at any point count.

Actual (observed in Chromium): (1) the composer height was measured only when the draft, the lg flag or `asking` changed, so 390 to 844 wide left the old height (5 of 7 new tests failed: clipped text in portrait, an over-tall box in landscape). (2) The polyline svg was `w-full` of a flex row that is as wide as the scroller's visible box while its 3rem columns overflow it; past about 6 points the line was squeezed into the first screenful and missed its dots (4 of 6 geometry tests failed at 7, 12 and 30 points). A 12-point chart drew a trend that no point supported.

## Repro

DMS commit `9435686`: `e2e/iphone-composer-rotate.spec.ts` and `e2e/iphone-line-geometry.spec.ts` from commit `24cbaf8` run against it. The weekly correctness review (a read-only Sonnet agent) reported the line chart as "inferred"; a geometry test turned it into a fact.

## Root-cause class

**A layout fact is cached against the state that was thought to cause it, not against the dimension it depends on.** The composer's height depends on the field's width; the code keyed it to the draft. The overlay's width depends on the content's width; the code sized it from the container's. Both pass every test that changes the state and never the width, and both are invisible at the one width a developer opens. A second instance appeared in the fix: a `ResizeObserver` callback that changes the observed element's own height raises "ResizeObserver loop completed with undelivered notifications" on every rotation; Playwright does not surface it as a pageerror, so only a `window` error listener sees it.

## Invariant

"Assert the artifact the user receives": the tests read boxes (text clipped, polyline vertex against dot centre in screen pixels), not a height formula. "Prove a gate can fail": each new test failed on the old code, and five mutations of the gap tests (observer left attached at lg, cap 130, effect without the lg flag, the overscroll rule at 1279px, no frame deferral) are all red. Seven gaps were found by the checker after the first green run: the cap was never asserted to be reached, the lg boundary (1023/1024) was unpinned, and an observer left attached at lg would have reset a person's `resize-y` drag.

## What shipped

Phone-only: a width-keyed `ResizeObserver` (one frame later) for the composer; the line row is `max(100%, count*3rem)` wide; the page-level overscroll lock moved into the below-lg media query (it had leaked to desktop; 3050 elements compared against the parent in 16 combinations, only the body overscroll longhands differ). 17 tests, Playwright 394 of 394.

## Agent shape

Orchestrator on the session model. Two read-only Sonnet agents with isolated context (a correctness review of the whole `apps/ui` diff; an independent checker of the fix with pristine copies and sha256 restores). The orchestrator wrote the tests first and the fixes. Opus was not needed: every step was verifiable from a terminal.
