# GRANT-WIRE: a missing-grant read raises the Allow / Cancel access ask (#204, refs #205)

- **Date:** 2026-09-23
- **Keywords:** crew, grant, access_ask, sse, dialog, GrantMissing, R-0011, workspace, runtime, playwright, #204, #205
- **Main idea:** GRANT-01..04 landed the store, the jail, the dialog and the smooth read, but nothing called `window.crewAskAccess`: a `ws_ls` / `ws_read` / `ws_glob` refused for a missing folder grant painted DENIED in the transcript and the founder never saw the ask. Now the one refusal an Allow can lift is raised as `GrantMissing` (a `WorkspaceError` subclass carrying the folder to ask for), the runtime emits one `access_ask` event on the space's existing SSE bus, and the page opens the GRANT-03 dialog for that folder with a reason. Root, `/`, the profile root, `..`, a history database, a write and a relative escape stay plain `WorkspaceError` and ask nothing. Nothing retries; on Allow the dialog's existing POST records the grant and the agent may re-issue the read on its next turn; on Cancel nothing changes.
- **Verify:** `python -m pytest tests/test_crew/test_grant_wire_access_ask.py tests/test_crew tests/contract -q -p no:cacheprovider`
- **Does not prove:** HT1 (founder walks Allow and Cancel on real Documents plus one open window) is a human gate and stays PENDING. A real Windows filesystem: a Windows-style path cannot be resolved on this POSIX host, so the Windows parent rule is asserted on the derivation string-level only. `python scripts/export_openapi.py --check` (needs `.[full]`; crew routes are not on the wire contract).
- **Cite:** Cortex#204 EPIC-GRANT-03 (dialog entry point `window.crewAskAccess`), #205 EPIC-GRANT-04, #203 EPIC-GRANT-02 (jail and R-0011 reasons), #202 EPIC-GRANT-01 (`normalise_folder`). Channel reused: `EventBus.emit` -> `GET /crew/spaces/{id}/events` SSE -> `listen()` in `index.html`. Did not edit `policy.py`, `session_grants.py`, `server.py`, `contract/`, `tests/contract`, `tests/invariants`, `.importlinter`, `.github/**`, `INDEX.md`, `STATUS.md`; GRANT-01..04 tests pass unmodified.

## Expected vs actual

| | Expected | Actual |
|---|---|---|
| Missing-grant `ws_read` | founder sees the ask for the containing folder | `GrantMissing.folder` is the typed path's parent after `normalise_folder`; the runtime emits `access_ask {space_id, folder, reason, tool}`; the page calls `crewAskAccess({session_id: space_id, reason, folders: [folder]})`; browser test sees one `li` with the folder path as text and the reason naming `ws_read` and the typed path |
| `ws_ls` / `ws_glob` on a directory | ask for that directory itself | `candidate.is_dir()` on the resolved path picks the typed path; `ws_glob` goes through `_split_glob` so the wildcard-free head is the folder asked for |
| Transcript still says why | DENIED R-0011 with the missing-grant reason, never the content | unchanged message text; asserted in the runtime and through `GET /crew/spaces/{id}/messages` in the browser test |
| Root, `/`, `C:\`, profile root | no ask | those are `GrantRefused` from the book before any lookup, still a plain `WorkspaceError`; a file directly under the profile root is `GrantMissing` with `folder=None` (its folder can never be granted), so nothing is emitted |
| `..`, history DB, write, edit, relative escape | no ask | raised before or after the grant lookup as plain `WorkspaceError`; the runtime checks `isinstance(exc, GrantMissing) and exc.folder`, never message text; asserted with nine cases and no leaked content |
| One event per refusal | exactly one | asserted on a bus subscription: one per `_run_tool`, two for two refusals, zero after an Allow |
| Cancel | POST `decision=cancel`, nothing else changes | browser test: exactly one POST `{session_id: <space>, kind: folder, decision: cancel, persist: false, path: <folder>}`, GET shows the cancel row only, dialog closed |
| Duplicate while open | no second dialog | two refusals in one run put two `access_ask` events on the wire; one dialog with one item; after Cancel the dialog stays closed (0.6 s) and only one POST exists; a fresh refusal after Cancel asks again |
| Never-grantable in the browser | nothing opens | a `ws_write` outside the space plus `ws_ls /` in one run: two DENIED rows, dialog never opens, no POST, no file written |
| Grant recorded on Allow | via the dialog's existing POST | not re-asserted here: GRANT-03 `test_browser_allow_paints_store_refusal_inline` and `test_browser_session_id_defaults_to_the_current_space_id` cover it and pass unmodified |

## Repro

```
manager tool call: ws_read path=/laptop/outside/secret.txt   (no grant)
transcript: DENIED: R-0011: '/laptop/outside/secret.txt' is outside the space folder and no folder grant ...
SSE:        event: access_ask
            data: {"space_id": "<space>", "folder": "/laptop/outside", "reason": "Manager asked ws_read on '/laptop/outside/secret.txt' and no folder grant covers it in this session. Allow reads of this folder for this session? ...", "tool": "ws_read"}
page:       #accessAsk[open], one item "folder /laptop/outside", reason painted
Cancel ->   POST /crew/session-grants {"session_id": "<space>", "kind": "folder", "decision": "cancel", "persist": false, "path": "/laptop/outside"}
manager tool call: ws_ls path=/   -> DENIED R-0011, no event
```

## Root-cause class

Missing wire between two finished parts. The refusal already said "Ask the operator to Allow the folder first" but no code path asked. Three decisions:

1. **Structural, not textual.** The grantable refusal is a subclass (`GrantMissing`) with a `folder` attribute. Matching the R-0011 message text would have coupled the ask to prose that GRANT-02 may reword.
2. **The folder comes from the typed path, never the resolved one.** GRANT-02 refuses to echo a resolved path because it can leak a link's target. The ask keeps that rule: a symlink at `/granted/link -> /outside/secret` asks for `/granted`, which after an Allow still resolves outside and stays refused (fail closed, and the refusal names the missing grant).
3. **Nothing retries.** The runtime emits and returns the DENIED text in the same turn. A retry loop would either block the run on a human or re-issue reads the founder has not decided on.

The Windows parent rule uses `PureWindowsPath` on the typed text when it carries a drive or a backslash, else `PurePosixPath`, so `C:\top.txt` asks nothing (parent is a drive root) and `C:\a\b.txt` asks `C:\a`.

## Invariant applied

- A refusal is never weakened: `_resolve_granted` raises the same messages in the same places; only the exception type of one branch changed, and it is still a `WorkspaceError` so every existing `except` and test holds.
- One store, one normaliser: the folder to ask for goes through `normalise_folder` with the book's `profile_roots`; the dialog's existing POST records the grant under the space id GRANT-02 looks up.
- A grant is not an approval: `policy.decide`, `_await_confirm` and the MCP branch are untouched.
- No new channel: the event rides `EventBus` and the existing `/events` SSE stream with the existing resync behaviour.
- Attacker text stays text: the folder and reason are painted through the GRANT-03 `textContent` path; the dedup handler sits inside the GRANT-03 JS block so the served-page assertions (no `innerHTML`, one `accessAskSettle("allow")` site, no `localStorage`) cover it.
- `CortexOS/**` imports no `packs.*`; `lint-imports` 3 kept, 0 broken.

## Verified vs assumed

- Verified: 8 new tests green (5 runtime / workspace, 3 real-browser through the real channel: HTTP message -> runtime refusal -> bus -> SSE -> dialog); full suite 2203 passed / 12 skipped / 4 xfailed on 21f5d84 (baseline 2195 / 12 / 4; delta is exactly the 8 new tests); GRANT-01..04 tests pass unmodified; `tests/contract` 95 passed; `ruff check CortexOS tests/test_crew` reports only the pre-existing I001 in `tests/test_crew/test_approvals.py` (not touched); `mypy` 41 errors in 26 files, the same count as before this change and none on a changed line; `lint-imports` 3 kept; `python scripts/check_versions.py` OK.
- Verified after merging 54c6a48 (BROWSER-GATE-CI): the `browser` fixture reuses `browser_gate_unavailable` from `test_session_grant_dialog.py` (skip locally, fail under `CORTEX_BROWSER_GATE=required`; 8 passed with it set here), the `page` fixture waits for boot's space selection plus the open SSE stream, and the browser tests of both files ran 6 times in parallel under load: 6 of 6 green (48 browser tests, 0 failures).
- Assumed: that a founder's real UACC session has the page open on the space the agent runs in (the event is per space, and a page on another space does not see it).
- Not done on purpose: no automatic retry after Allow, no change to refusal semantics or the confirm ladder, no new event bus, no ask for a write, no edits to GRANT-01..04 tests.
- HT1: PENDING. Founder gate, not ticked and not claimed here.
