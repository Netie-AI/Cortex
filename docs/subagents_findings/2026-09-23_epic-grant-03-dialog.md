# EPIC-GRANT-03: Crew Allow / Cancel session access dialog over session grants (#204)

- **Date:** 2026-09-23
- **Keywords:** crew, grant, session-grants, dialog, allow, cancel, a11y, textContent, playwright, uacc, #204
- **Main idea:** The Crew page now carries one native `<dialog>` that lists the exact folder paths, window titles (with pid) and Office files Crew asks to read, states the reason, says files stay on this device, and waits for Cancel or Allow. Each decision is POSTed one row per item to GRANT-01 `POST /crew/session-grants` with `persist=false` and `session_id` defaulting to the current Crew space id (GRANT-02 checks grants by space id). Cancel never posts allow. A 4xx from the store is painted inline on the refused item on Allow, and toasted plus kept in `crewAskAccess.last.refused` on Cancel. Paths and titles are painted with `textContent` only. No second store, no jail expansion, no persist-on-device.
- **Verify:** `python -m pytest tests/test_crew/test_session_grant_dialog.py tests/test_crew tests/contract -q -p no:cacheprovider`
- **Does not prove:** HT1 (founder walks Allow and Cancel on real Documents plus one open window) is a human gate and is PENDING, not claimed. The browser gate ran in headless Chromium 141 via python-playwright 1.56.0 over the bundled `/opt/pw-browsers`; whether CI has that package is not known from here (the browser tests skip loudly, they do not fake a pass). No live UACC `list_windows`; window items are whatever the caller passes.
- **Cite:** Cortex#204 EPIC-GRANT-03. Store and endpoint from `CortexOS/crew/session_grants.py` (EPIC-GRANT-01, #202). Dialog chrome distilled from the existing `takeover` HITL box in `index.html` and its tokens in `crew.css` (kicker, actions row, `.ok` and `.ghost.deny` buttons, `--scrim`). Did not edit `workspace.py`, `policy.py`, `runtime.py`, `session_grants.py`, `contract/`, `tests/contract`, `tests/invariants`, `.importlinter`.

## What landed

| File | Change |
|---|---|
| `CortexOS/crew/ui/index.html` | `<dialog id="accessAsk" role="dialog" aria-modal="true" aria-labelledby aria-describedby>` after the takeover overlay; `window.crewAskAccess(req)` entry point and handlers next to the takeover handlers |
| `CortexOS/crew/ui/crew.css` | `.access-ask*` block using only live tokens (`--desk --ink --hitl --line --well --lease --dead --ok --scrim --mono --font`) |
| `tests/test_crew/test_session_grant_dialog.py` | 2 served-page tests, 5 real-browser tests (uvicorn on a free port plus headless Chromium) |

Entry point:

```
window.crewAskAccess({
  session_id,            // optional, defaults to state.spaceId
  reason,                // painted with textContent
  folders: ["C:\\Users\\ops\\Documents", {path, destination}],
  windows: [{title, pid}],
  files:   ["C:\\...\\Q3.xlsx"],
  destination            // optional; "local" or empty means nothing is shown
}) -> Promise<"allow" | "cancel">
window.crewAskAccess.last -> {decision, granted: [rows], refused: [rows + reason]}
```

Calls are serialised: a second ask waits for the first dialog to close.

## Expected vs actual

| | Expected | Actual |
|---|---|---|
| List exact paths and titles, reason, wait | founder sees every item and the why before deciding | `#accessAskItems` gets one `li` per item (kind chip, path or `title (window, pid N)`), `#accessAskWhy` gets the reason; `showModal()` blocks the page until Cancel or Allow; nothing is POSTed before a click or Escape |
| Cancel does not expand the jail | no allow row ever written on Cancel | Cancel and Escape both call `accessAskSettle("cancel")`; the only `accessAskSettle("allow")` call site is the Allow button handler (asserted by count). Browser test: three items, Cancel clicked, all three POST bodies carry `decision=cancel`, GET shows cancel rows only |
| Files stay on this device | statement always visible; named destination only when a row carries one other than local | `#accessAskLocal` is static copy: "Files stay on this device. Allow grants reads only, for this session; nothing is written outside the space and nothing here is uploaded." A per-item or shared `destination` that is not `local` / `this device` paints `to <name>` on that item and a note counting them; `destination: "local"` paints nothing |
| Live tokens, distilled ask pattern | no third-party chrome or wording | Block reuses `takeover__kicker` and `takeover__actions`, the `.ok` and `.ghost.deny` buttons and `:focus-visible` ring; test asserts no hard-coded colour inside the `.access-ask` CSS block |
| Session-only v1 | every POST `persist=false` | `accessAskBody` hard-codes `persist: false`; test asserts `persist: true` is absent from the JS and every captured body has `persist is False` |
| GRANT-02 keying | grants land under the Crew space id | `session_id` defaults to `state.spaceId`; browser test sets the space id, calls the entry point without `session_id`, and asserts the POST body and the GET store both carry the space id |
| Hostile title | literal text, no execution | `<img src=x onerror=alert(1)>` renders as the `li` text; `#accessAskItems img` count is 0; no `page.dialog` (alert) fired; the store row holds the literal string |
| Refusal visible | a refused item is never silently dropped | Allow: item gets `access-ask__item--refused`, `refused: <server reason>` text, `#accessAskRefused` (role=alert) counts them, Allow hides, Cancel becomes Close, dialog stays open. Cancel: refusals toasted (`.toast--bad`) and kept in `crewAskAccess.last.refused`, dialog closes |
| A11y | focus in, focus out, Escape, real buttons | Focus lands on Cancel (safe default) on open and returns to the previously focused element on close (asserted `#clearChat`); Escape goes through the `cancel` event and, if the platform closes the dialog without a cancelable event, the `close` event still settles as Cancel; both buttons are `<button type="button">` |

## Repro

```
# in the served page console
await crewAskAccess({reason: "Read the deck", folders: ["C:\\Users\\ops\\Documents"], windows: [{title: "<img src=x onerror=alert(1)>", pid: 4242}]})
# click Cancel ->
POST /crew/session-grants {"session_id":"<space id>","kind":"folder","decision":"cancel","persist":false,"path":"C:\\Users\\ops\\Documents"}
POST /crew/session-grants {"session_id":"<space id>","kind":"window","decision":"cancel","persist":false,"title":"<img src=x onerror=alert(1)>","pid":4242}
GET  /crew/session-grants?session_id=<space id> -> two rows, both decision cancel
```

## Root-cause class

New surface, no defect fixed. Two self-inflicted test findings while landing:

1. `page.wait_for_selector("#accessAsk:not([open])")` waits for a visible element and a closed `<dialog>` is hidden, so every close wait timed out. Replaced by a wait on the `open` flag.
2. The first cut held the dialog open in the Close state on any refusal, including on Cancel. With a folder plus a file, the file's cancel row is refused by the store (its folder was never allowed), which left the founder stuck after clicking Cancel. Cancel must be as easy as Allow and a refusal to record a cancel grants nothing, so Cancel now closes and surfaces refusals as toasts plus `last.refused`; Allow keeps the inline hold because a refused item changes what was granted.

## Invariant applied

- Cancel never writes allow: one Allow call site, asserted by string count and by the captured POST bodies.
- Attacker text stays text: no `innerHTML`, `insertAdjacentHTML` or `outerHTML` in the dialog JS (asserted on the served page), and a real browser confirms the hostile title neither executes nor creates an element.
- One store: the dialog only talks to `/crew/session-grants`; no `localStorage`, no second catalog.
- Session-only: `persist` is a literal `false` in the one body builder.
- `CortexOS/**` imports no `packs.*`; `lint-imports` 3 kept, 0 broken.

## Verified vs assumed

- Verified: `tests/test_crew/test_session_grant_dialog.py` 7 passed (2 served-page, 5 browser); `tests/test_crew` + `tests/contract` 526 passed; full suite `python -m pytest tests/ -q -p no:cacheprovider` 2162 passed, 12 skipped, 4 xfailed (baseline 2151 / 13 / 4: plus 7 new, plus 4 formerly-skipped repo tests that now run because python-playwright is installed in this environment, see below); `ruff check CortexOS tests/test_crew` reports only the pre-existing I001 in `tests/test_crew/test_approvals.py` (not touched); `lint-imports` 3 kept; `python scripts/check_versions.py` ok.
- Environment note: `playwright==1.56.0` (python package) was pip-installed here to drive the bundled Chromium 1194 at `/opt/pw-browsers`; `playwright install` was NOT run. That install is why one baseline skip (playwright-dependent repo tests under `tests/reliability` / `tests/test_discovery`) now passes; it is not a repo change.
- Assumed: no existing Crew code path surfaces a laptop-reach request yet, so nothing calls `crewAskAccess` automatically; the entry point is exposed on `window` for the runtime or GRANT-02 wiring to call. `mypy` not re-run (no Python source changed outside a test file).
- Not done on purpose: no persist-on-device option, no Pointer HUD, no jail expansion, no UACC call, no edits to `workspace.py`, `policy.py`, `runtime.py`, `session_grants.py`.
- HT1: PENDING. Human gate, not ticked.
