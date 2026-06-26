<!-- generated 2026-06-26T01:26:08+00:00 -->
# CURSOR_HANDOFF — Builder Session Startup
**Read this file first.** Then `STATUS.md` → `CONTEXT.md` → `docs/dms/F5_PLAN.md`.

---

## Your role
You are the **builder**. One feature per run. Sequential only for dependent features.

## Current state
| Item | Status |
|---|---|
| V0–V1, F1, F7, F2, F3, F4 | Shipped, gates PASS |
| **F5 compliance gate** | **In progress** |
| After F5 PASS | F6 skill capture |

## Startup checklist
- [ ] `git checkout dms-v2 && git pull`
- [ ] `pip install -e ".[dev,api,dms]"`
- [ ] `pytest -q` — expect 134+ passed, 4 skipped

## Build loop (mandatory)
1. Plan → smallest diff
2. Implement + tests
3. `pytest -q` green
4. Append `CHANGELOG_DMS.md`
5. Update `STATUS.md` + `python scripts/handoff.py --write`
6. Paste `CLAUDE_HANDOFF.md` to Claude → wait for PASS
7. **Stop** — do not start F6 until Gate F5 PASS

## Active spec
`docs/dms/F5_PLAN.md` — supervisor APPROVED

## Demo
```powershell
pip install -e ".[dev,api,dms]"
.\demo\run_demo.ps1
```
