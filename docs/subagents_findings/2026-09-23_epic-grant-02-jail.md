# EPIC-GRANT-02: jail expands only into Allow-ed session grants (#203)

- **Date:** 2026-09-23
- **Keywords:** crew, grant, jail, workspace, session-grants, R-0011, symlink, prefix-trap, confirm, destination, #203
- **Main idea:** `ws_ls` / `ws_read` / `ws_glob` may leave the space folder only into a folder this session recorded with decision `allow` in the GRANT-01 `SessionGrantBook`. The check runs on the resolved real path (symlinks followed) and is component-wise, so `D:\work` admits neither `D:\work-evil` nor `D:\work\..\x` nor a link that points out. A Cancel, another session's grant, a drive root, `/`, the user profile root and ungranted AppData are refused with a reason that names R-0011 and the missing grant. `ws_write` / `ws_edit` outside the space stay refused. A grant is not an approval: a click or type_text still walks the CONFIRM ladder and `CORTEX_COMPUTER_CONTROL` is untouched. Every grant row now carries `destination: "local"`; no off-device path exists.
- **Verify:** `python -m pytest tests/test_crew/test_grant_jail.py tests/test_crew tests/contract -q -p no:cacheprovider`
- **Does not prove:** a real Windows filesystem (`Path.resolve()` on NTFS junctions and `\\?\` prefixes was not exercised; the Windows-style cases are string-level through `PureWindowsPath`); the GRANT-03 dialog recording grants with `session_id` equal to the space id (assumed, see below); `python scripts/export_openapi.py --check` (needs `.[full]`; crew routes are not in `CONTRACT_ROUTE_IDS`).
- **Cite:** Cortex#203 EPIC-GRANT-02, depends on #202 EPIC-GRANT-01. Store and normaliser reused from `CortexOS/crew/session_grants.py` (no second store, no second normaliser). Did not touch `CortexOS/crew/ui/**`, `contract/`, `tests/contract`, `tests/invariants`, `.importlinter`, `INDEX.md`, `STATUS.md`.

## Expected vs actual

| | Expected | Actual |
|---|---|---|
| Inside the space | relative paths unchanged | `resolve()` keeps the old relative branch byte for byte; `../x` still "path escapes workspace"; `tests/test_crew/test_workspace.py` and the two `ws_*` runtime tests pass unmodified |
| Outside, ungranted | refused, says why | `DENIED: R-0011: '<path>' is outside the space folder and no folder grant with decision allow covers its real path (links followed) in this session; missing grant: folder allow for that path or a parent. Ask the operator to Allow the folder first` |
| Outside, Allow-ed folder | ls / read / glob succeed | `granted_folder_for(space_id, resolved)` returns the folder; `ls`, `read` of a file and of a sub-folder file, and an absolute `glob` all paint the content in the transcript |
| Prefix trap | `D:\work-evil` refused under a `D:\work` grant | the lookup builds the candidate's parents with `PureWindowsPath` / `PurePosixPath` and intersects keys, so only a whole component matches. Asserted on POSIX through the tool path and on Windows-style strings through the book |
| `..` escape | refused | refused twice: the workspace refuses any `..` component before touching the filesystem, and `normalise_folder` refuses it again on the resolved path |
| Symlink escape | refused | `Path(text).resolve()` follows the link; the real path is outside the grant so the lookup returns `None`. A second net (`candidate.relative_to(Path(folder).resolve())`) runs on a different mechanism. Absolute `glob` also drops any hit whose real path leaves the base. Symlinks were created on this Linux host (no skip); the test skips loudly on an OS that cannot make one |
| Cancel | admits nothing | `_granted_folders` filters on `decision == allow`; a later Cancel upserts over the Allow and closes it again |
| Other session | admits nothing | the space id is the grant session; `theirs` grant does not open `mine` |
| Write outside | refused | `ws_write` / `ws_edit` with an absolute path: `R-0011: absolute path '<p>' refused: write stays inside the space folder; a session folder grant admits reads only (ls, read, glob)`. The granted file is byte-identical afterwards |
| `C:\`, `/`, profile root, AppData | refused, says why | `C:\` and `/` raise `GrantRefused("... drive root ...")` / `("... filesystem root ...")` from the book before any lookup and the tool path paints DENIED R-0011; the profile root paints `the user profile root is not a grantable folder`; ungranted AppData under it paints the missing-grant reason and never the file content |
| Click / type with a grant | still CONFIRM | master off: DENY "computer control is off" and `mcp.master_on` stays False. Master on + armed + folder and window grants recorded: `uacc.click` and `uacc.type_text` both appear as pending confirms, are denied, and the fake client is never called. `policy.decide` has no grant input at all |
| Destination | on the row, default local | `_build` stamps `destination: "local"` on every row; `_load` overwrites whatever a stale file holds (a `cloud-worker` value reads back as `local`); HTTP POST and GET show it |

## Repro

```
book.record(space_id, kind="folder", path="/laptop/work")
ws_read  path=/laptop/work/q3.xlsx        -> "# ... granted sheet"
ws_read  path=/laptop/work-evil/trap.txt  -> DENIED: R-0011: ... no folder grant with decision allow ...
ws_read  path=/laptop/work/../outside/s   -> DENIED: R-0011: absolute path ... '..' traversal is refused
ws_read  path=/laptop/work/link.txt (-> /laptop/outside/secret.txt) -> DENIED: R-0011: ...
ws_write path=/laptop/work/new.txt        -> DENIED: R-0011: ... admits reads only (ls, read, glob)
mcp_uacc_click with the grant present     -> pending confirm; denied; not called
```

## Root-cause class

New boundary, no defect fixed. Two design decisions worth stating:

1. **The grant session is the space.** The runtime has no session id of its own; the space is the chat the founder is in and the UI already carries `state.spaceId`. `CrewRuntime` looks grants up under `ctx.space_id`. GRANT-03 must POST `/crew/session-grants` with `session_id` set to the space id or its Allow will not open anything (fail closed, and the refusal names the missing grant).
2. **Writes outside the space stay refused.** The ticket text allows write inside an Allow-ed grant. An Allow recorded by the GRANT-03 dialog is asked as a read ("Crew needs laptop folders"), the catalog says nothing about modifying, and a write to a founder's real file is a mutation with no confirm on it. Keeping it refused is the fail-closed choice; a write grant kind or a confirm on write can lift it later without changing any refusal added here.

One pre-existing quirk kept on purpose: the old `resolve()` stripped a leading `/` so `/notes.md` silently meant `notes.md` inside the space. Now a leading `/`, `\` or a drive letter means "outside the space", which is the only reading under which a jail check can be applied. Nothing in the suite depended on the old reading.

## Invariant applied

- Fail closed on the resolved path: the filesystem is consulted only through `Path.resolve()` and the grant is matched against that result, never against the string the model typed.
- Component-wise matching only (`_parents` + `_key`), reused from the office_file rule; no `startswith`.
- A refusal never echoes the resolved path: naming a link's target would leak a filename from a folder that was never granted.
- Grants live in one store (`SessionGrantBook`) and are looked up through one method (`granted_folder_for`); `policy.decide` has no grant parameter, so a grant cannot reach master / arm / confirm.
- `CortexOS/**` imports no `packs.*`; `lint-imports` 3 kept, 0 broken; `tests/contract` 95 passed.

## Verified vs assumed

- Verified: 17 new tests green alone and inside the full suite; full suite 2172 passed / 12 skipped / 4 xfailed (baseline on b14f014 was reported as 2151 / 13 / 4; the delta beyond the 17 new tests is one previously skipped test now running plus 3 the baseline note did not account for, none in `tests/test_crew`); `tests/test_crew` 441 passed; `tests/contract` 95 passed; `ruff check CortexOS tests/test_crew` reports only the pre-existing I001 in `tests/test_crew/test_approvals.py` (not touched); `mypy` 41 errors in 26 files, none in the five changed modules or the new test (pre-existing); `lint-imports` 3 kept; `python scripts/check_versions.py` ok.
- Assumed: GRANT-03 uses the space id as `session_id`; a real NTFS host resolves junctions the way `Path.resolve()` promises; no contract version bump (crew routes are outside `cortex_contract`).
- Not done on purpose: no skip-confirm on reads (GRANT-04), no Browser History sqlite, no Excel clicking, no UI, no off-device destination, no write grant kind.
