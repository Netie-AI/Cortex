# EPIC-GRANT-04: granted reads skip per-read confirm; attach open windows, never launch (#205)

- **Date:** 2026-09-23
- **Keywords:** crew, grant, smooth-read, attach_window, list_windows, openpyxl, ws_read_xlsx, history, places.sqlite, R-0011, R-0015, confirm, #205
- **Main idea:** After an Allow is recorded, Crew lists, reads and globs the granted folder with no second per-read confirm. No confirm was removed because none existed: `ws_*` tools are crew-internal, `policy.decide` returns allow for them, and `_execute_tool` never routes them through `_await_confirm`; a test now pins that by making any confirm on a granted read raise. A granted, already-open window is attached by verifying its pid and title against a live UACC `list_windows` result and handing back a read handle; the only MCP tool that path may call is `list_windows`, and a test asserts the client never receives a launch-like name. A granted `.xlsx` is opened as data with openpyxl (read-only, values only, 2 MiB cap), never through the Excel UI. A browser history database (Chrome/Edge `History`, Firefox `places.sqlite`, with sqlite sidecars) is refused even inside an Allow-ed folder because v1 has no grant kind for it. A click or type_text with folder and window grants present still walks the CONFIRM ladder; that code is byte-for-byte unchanged.
- **Verify:** `python -m pytest tests/test_crew/test_grant_smooth_read.py tests/test_crew/test_grant_jail.py tests/test_crew tests/contract -q -p no:cacheprovider`
- **Does not prove:** the shape of a real UACC `list_windows` result (the JSON keys and the plain-text fallback are assumed; see below); a real Windows host; HT2, HT3, HT4 (founder gates, PENDING, not ticked); `python scripts/export_openapi.py --check` (needs `.[full]`; crew tools are not on the wire contract).
- **Cite:** Cortex#205 EPIC-GRANT-04, depends on #202 GRANT-01 (store), #203 GRANT-02 (jail), #204 GRANT-03 (dialog). Store and lookups reused from `CortexOS/crew/session_grants.py`; jail reused from `CortexOS/crew/workspace.py`; gate reused from `CortexOS/crew/policy.py` (`decide` for `list_windows` is the same floor every capture tool takes). Did not touch `CortexOS/crew/ui/**`, `contract/`, `tests/contract`, `tests/invariants`, `.importlinter`, `INDEX.md`, `STATUS.md`. No skill note was needed; fill-form stays F29.

## Expected vs actual

| | Expected | Actual |
|---|---|---|
| Granted read, no second confirm | ls / read / glob paint the content; no confirm row, no confirm event | `_await_confirm` is replaced by a raiser in the test rig; `ws_read`, `ws_ls`, `ws_glob` on the granted folder all succeed and `pending_confirms` is empty. Nothing was removed: the ws_* branch in `_execute_tool` sits before the MCP branch and after a `policy.decide(server=None)` that returns `allow, crew-internal tool` |
| Ungranted read | still refused, still no confirm | `DENIED: R-0011: ... no folder grant with decision allow ...`, the secret never paints, and the confirm raiser is never hit |
| Attach a granted live window | a read handle, one `list_windows` call, no launch | `ATTACHED {"attached": true, "pid": 4242, "title": "Budget - OneNote", "handle": 991, "grant_id": ..., "via": "uacc.list_windows", "launched": false}`; `client.called == [("list_windows", {})]`; the fake advertises `launch_app`, `open_application`, `start_process`, `activate_window` and none is called |
| Click / type after an attach, grants present | still CONFIRM | in the same run `uacc.click` and `uacc.type_text` both appear as pending confirms, are denied, and the fake is not called for them. The MCP branch of `_execute_tool` and `_await_confirm` are unchanged |
| No window grant | refused before any MCP call | `DENIED: R-0015: no window grant with decision allow for pid=4242 title='Budget - OneNote' ...; missing grant: window allow ...; Crew never launches OneNote, Word, a PDF viewer or Explorer: open it yourself and Allow the window`; `client.called == []`. Another space's window grant opens nothing |
| Stale pid (same title, new pid) | refused, nothing launched | `DENIED: R-0015: window pid=4242 title='Budget - OneNote' is granted but not open now (...); Crew does not launch or reopen it`; only `list_windows` was called |
| Title changed since the grant | refused | `Budget - Word` granted, live `Budget.docx - Word`: refused with the same not-open reason. Exact-after-normalisation is the rule (see below) |
| Master off | refused, never enabled | `DENIED: R-0015: computer control is off: set CORTEX_COMPUTER_CONTROL=1 and restart; nothing was launched`; `mcp.master_on` stays False; the client is not called; `spec.armed` is untouched |
| UACC disarmed / not catalogued | refused, never armed | `... MCP server 'uacc' is not armed ...; nothing was launched` with `spec.armed` still False and `starts == 0`; with the client popped, `MCP server 'uacc' is not catalogued, so no window can be verified; nothing was launched` |
| Granted `.xlsx` | cells as data, no Excel | `# <path> sheet 'Q3' rows 1-3 of 3 (sheets: Q3, Notes; read as data via openpyxl, not Excel)` then `sku\tqty\tprice`, `SKU-ALPHA\t12\t3.5`, `SKU-BETA\t7`; `sheet=Notes` reads the second sheet; a missing sheet is refused naming the sheets; no confirm |
| Ungranted `.xlsx` | refused | `DENIED: R-0011: ...` and no cell paints |
| Size cap, wrong extension, corrupt file | refused with the reason | `workbook exceeds 16 bytes (...)` under a monkeypatched cap; `not a workbook this reader opens as data: ... (.xlsm, .xlsx)` for `notes.md`; `cannot open ... as a workbook: ...` for `b"not a zip"` |
| History sqlite inside a granted folder | refused, says why | `DENIED: R-0011: '<path>' refused: 'History' is a browser history database (Chrome/Edge History, Firefox places.sqlite) and a folder grant does not admit it; missing grant: history (no such grant kind exists in v1, a silent History read is out of scope)` for `History`, `History-journal`, `Archived History`, `places.sqlite`, `places.sqlite-wal`; `ws_ls` of the file path is refused the same way; `Bookmarks`, `history.txt`, `MyHistory` and `notes.md` in the same grant still read; a link `innocent.db -> History` is refused on the resolved name |

## Repro

```
book.record(space_id, kind="folder", path="/laptop/work")
ws_read  path=/laptop/work/notes.md               -> "# ... granted notes"   (no confirm row)
ws_read_xlsx path=/laptop/work/q3.xlsx             -> "# ... sheet 'Q3' ... SKU-ALPHA\t12\t3.5"
ws_read  path=/laptop/work/User Data/Default/History -> DENIED: R-0011: ... browser history database ...
book.record(space_id, kind="window", title="Budget - OneNote", pid=4242)
attach_window title="budget - onenote" pid=4242    -> ATTACHED {... "via": "uacc.list_windows", "launched": false}
                                                     (fake.called == [("list_windows", {})])
attach_window with live pid 5151 for that title    -> DENIED: R-0015: ... not open now ... does not launch
mcp_uacc_click after the attach                    -> pending confirm; denied; not called
```

## Root-cause class

New reach on an existing boundary; no defect fixed. Three decisions worth stating:

1. **No confirm was invented and none was removed.** The ticket asks that a granted read need no second confirm. Tracing `_execute_tool` after GRANT-02 shows the `ws_*` tools never took one (crew-internal, `policy.decide` allow, dispatched before any `_await_confirm`). The change is a pinning test, not a code path. Removing a confirm that does not exist would have been a fiction, and adding one only to remove it would have moved the mutating ladder, which the ticket forbids.
2. **Attach is verify-only and its one tool is pinned.** `attach_window` looks up the window grant for the space (pid equal, title equal under the rule below), then takes the same `policy.decide("list_windows", ...)` floor every capture tool takes (master, then arming; neither is flipped), then makes exactly one `uacc.list_windows` call and matches the grant against the live rows. There is no launch, start, open, activate or focus call on the path, `ATTACH_TOOL` is a module constant asserted equal to `list_windows`, and the test's fake advertises launch-like tools so a wrong call would be visible. If the window is not live the answer is a refusal that tells the operator to open it themselves (R-0015).
3. **Excel is data, not a window.** `ws_read_xlsx` resolves through the same jail as `ws_read` (relative stays in the space, absolute needs a folder grant, History is refused) and then hands the resolved path to openpyxl in `read_only` + `data_only` mode. No COM, no UI, no `mcp_*` call. openpyxl is already a core dependency (`pyproject.toml` line 32), so nothing was added.

**Title match rule** (documented in `granted_reach.py`): both titles are casefolded, stripped, and internal whitespace runs collapse to one space; they must then be equal. The pid must match exactly. A renamed window (`Book1` became `Book1.xlsx`) is refused so the operator grants the window they now see rather than Crew guessing. On a plain-text `list_windows` result the line must hold the pid as a whole-number token (`14242` is not `4242`) and contain the normalised title.

**History rule**: file name (after `Path.resolve()`, so links are followed) matches `^(history|archived history|places\.sqlite)(-journal|-wal|-shm)?$` case-insensitively. Applied only to the granted (absolute) branch of `resolve`; the space's own folder is unaffected. Listing a directory that contains `History` still shows the name (a name is not a read); resolving that file for `ls` or `read` is refused.

## Invariant applied

- A grant is not an approval: `policy.decide` has no grant input; the MCP branch of `_execute_tool` and `_await_confirm` are unchanged; click and type_text still CONFIRM with folder and window grants present.
- Fail closed on liveness: a window is attached only when the live `list_windows` row matches the grant on pid and title; a stale pid, a renamed title, master off, a disarmed or missing server, a tool error and an unparseable result all refuse with a reason that says nothing was launched.
- One tool on the attach path (`ATTACH_TOOL == "list_windows"`, in `READ_ONLY_TOOLS`); never auto-enable (`master_on` and `spec.armed` are read, never written).
- One store, one lookup: window grants are read through `SessionGrantBook.grants_for` under the space id; folder grants still go through `granted_folder_for`.
- Reads only: `ws_read_xlsx` takes `REACH_READ`; `GRANT_WRITE_TOOLS` is unchanged, so a write outside the space stays refused.
- `CortexOS/**` imports no `packs.*`; `lint-imports` 3 kept, 0 broken.

## Verified vs assumed

- Verified: 16 new tests green alone and inside the full suite; full suite 2195 passed / 12 skipped / 4 xfailed (baseline on 164be8e: 2179 / 12 / 4; delta is exactly the 16 new tests); `tests/test_crew/test_grant_jail.py` green unmodified; `tests/contract` 95 passed; `ruff check CortexOS tests/test_crew` reports only the pre-existing I001 in `tests/test_crew/test_approvals.py` (not touched); `mypy` 41 errors in 26 files, none in the four changed modules or the new test (pre-existing); `lint-imports` 3 kept; `python scripts/check_versions.py` OK.
- Assumed: the JSON keys UACC `list_windows` uses (`pid` / `process_id`, `title` / `name`, `hwnd` / `handle` are all accepted; a plain-text result falls back to a line match); that a real UACC `list_windows` is itself read-only (it is listed in `READ_ONLY_TOOLS` already); that openpyxl's cached values are what the founder wants for formula cells (`data_only=True`; a workbook never recalculated by Excel returns None for those cells).
- HUMAN_TEST_GATES: HT2 PENDING, HT3 PENDING, HT4 PENDING. Founder gates; not ticked and not claimed here.
- Not done on purpose: all-apps access, auto-launch, unattended Act, a second mouse, CF Computer, Excel Copilot, a History grant kind, any change under `CortexOS/crew/ui/**`, a write grant kind, a skill note (fill-form stays F29).
