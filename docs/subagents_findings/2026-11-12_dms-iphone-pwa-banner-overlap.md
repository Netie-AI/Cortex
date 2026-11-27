# DMS iPhone PWA: a fixed phone button covered the demo banner

Date: 2026-11-12. Repo: DMS `apps/ui`, branch `claude/awesome-euler-12si4n` (IPHONE-PWA-01).

## Expected vs actual

- Expected: at phone width the "DEMO ASK MODE" banner is fully readable (DMS CLAUDE.md rule 11: permanent, unmissable).
- Actual: the Sources opener was `fixed` at `top-12 right-3`. `top-12` is the top bar height, so with a banner present the button sat on the banner and hid its right third. Chat also carried an 8rem right gutter to make room for the button, which cost a third of a 390px screen.

## Repro

1. Start the DMS API with `DMS_ASK_MODE=demo` and the UI dev server.
2. Open `/` at 390x844.
3. On the commit before the change, "SOURCES" overlaps the banner text. `apps/ui/e2e/iphone-pwa.spec.ts` ("Sources sits in the chat composer") fails with `Expected: >= 98, Received: 48`.

## Root-cause class

A `fixed` offset copied from one piece of chrome's height breaks when optional chrome (a banner) renders under it. The existing guard (`STUDIO-MOBILE-03`) only checked overlap with the `h1`, so it passed.

Second instance of a known class: the first draft of the new test passed on the broken UI, because it measured before `/health` returned and the banner rendered. It only failed after waiting for network idle.

## Invariants that applied

- Prove a gate can fail before trusting it green (the race above).
- Assert the artifact the user receives (rendered boxes at phone width, not class strings).
- Silent fallback is a lie (a covered demo banner is a hidden degradation).

## Fix

The phone opener renders in the chat composer row (`SourcesOpenButton`), the desktop rail tab is unchanged. The gutter is removed.

## Not verified

Chromium iPhone emulation only. No real iPhone, no live Cortex ask.

## Subagents

None spawned. One session, one context.
