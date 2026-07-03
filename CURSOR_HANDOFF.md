<!-- generated 2026-07-03T04:27:53+00:00 -->
<!-- generated 2026-07-03 -->
# CURSOR_HANDOFF — Builder Session Startup
**Read this file first.** Then `STATUS.md` → `CONTEXT.md` → `docs/dms/F5_PLAN.md` (gate) or BUILD_PLAN (next feature).

---

## Your role
Builder. One feature per run. **Do not start F6 until Gate F5 PASS.**

## Current state
| Item | Status |
|---|---|
| V0–V1, F1, F7, F2, F3, F4 | Shipped, gates PASS |
| **F5 compliance gate** | Shipped — **awaiting Claude PASS** |
| Demo | Live: `.\demo\run_demo.ps1 -Fast` |
| After F5 PASS | F6 skill capture |

## Startup checklist
- [ ] `git checkout dms-v2 && git pull`
- [ ] `pip install -e ".[dev,api,dms]"`
- [ ] `pytest -q` — expect **141 passed, 4 skipped**

## Build loop
1. Plan → smallest diff
2. Implement + tests
3. `pytest -q` green
4. Append `CHANGELOG_DMS.md`
5. Update `STATUS.md` + `python scripts/handoff.py --write`
6. Paste `CLAUDE_HANDOFF.md` to Claude → wait for PASS
7. **Stop** until gate clears

## Active work
**Gate F5 only** — no new features until supervisor PASS. If fixing F5 blockers, stay in F5 scope.

## Demo
```powershell
.\demo\run_demo.ps1 -Fast
```
Guide: `docs/DEMO.md`

## Ponytail
`PONYTAIL_DEFAULT_MODE=full` — see `docs/PONYTAIL.md`
