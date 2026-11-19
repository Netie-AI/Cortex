# DMS phone: a contrast gate that samples only first-load states certified a dark theme with white text on a neon fill

Date: 2026-11-18. Repo: DMS (`Netie-AI/dms`), PR #510, ticket #512 (EPIC-008). No answer-path code involved.

## Expected vs actual

Expected: on a phone, every enabled piece of text meets WCAG AA (4.5:1, 3:1 large) in both modes, and a gate says so.

Actual: no gate existed. Measuring first (computed styles, backgrounds composited up the ancestors, opacity applied, disabled controls exempt, 10 routes, 390px and 1280px, both modes) found three enabled failures in the graphite mode on a phone: Studio "+" 1.34:1 (`text-white` on #00ff87), Library selected-row count and bullet 3.43:1, Studio refusal reason 3.9:1. The tempting fix, a lighter `--color-ink-muted`, was wrong: #888 on the dark panel is 5.33:1; the failures were muted ink on soft fills and white on the accent. After the first fix and a green 24-test gate, an independent checker found four more the gate could not see.

## Repro

`apps/ui/e2e/iphone-contrast.spec.ts` on DMS commit `ad33cce` (the audit, without the fix). Checker's four: graphite chart answer, tap the first bar (value span 3.43:1); a vertical bar and a line chart with an item selected (axis label 3.43:1); empty inputs (placeholder 3.12:1 cream, 4.48:1 graphite).

## Root-cause class

**A gate that samples the states reachable on first load certifies only those states.** Three things hid the rest: (1) interaction states (a selected row sits on a different background), (2) text that is not a text node (a placeholder is a pseudo-element, and Tailwind writes it as `color-mix(...)`, which an `rgb()` parser silently skips), (3) a guard that could not fail (the self-test probed a disabled button, so widening the exemption to every control stayed green; a wrong route rendered nothing and passed vacuously). A measure-first rule also mattered: the planned token change would have fixed nothing.

## Invariant

"Prove a gate can fail before trusting it green." Every exemption in the gate needs a self-test that an enabled counterpart is reported; every route needs a minimum-coverage assertion; every colour parser must fail loudly on a format it cannot read (here: rasterise with a canvas). "Silent fallback is a lie": a parser that skips what it cannot read is a silent fallback.

## What shipped

Phone-only fixes below `lg` (desktop pinned unchanged by 16 page-states compared against the parent commit); 30 tests, each RED on the old code; `max-lg:text-[var(--color-on-accent)]`, `max-lg:text-[var(--color-ink)]` on muted text over soft fills, one phone-only `::placeholder` rule. Open: the same white-on-neon text exists on the desktop dark theme in 5 buttons (desktop is pinned, so a product-owner decision); first-paint flash of cream for a saved Operate mode.

## Agent shape

Orchestrator on the session model. Builder: the orchestrator, after a measure-first audit. One independent Sonnet checker that did not write the change (pristine copies and sha256 restores, 8 mutations, 16-state desktop comparison, blind-spot hunt); a second pass by the orchestrator closed the findings. Opus was not needed: every step was verifiable from a terminal.
