<!-- generated 2026-07-03T05:06:23+00:00 -->
# CURSOR_HANDOFF — Builder Session Startup
**Read this file first.** Then `STATUS.md` → `CONTEXT.md` → `docs/dms/GATE_F6_PACKET.md`.

---

## Your role
Builder. One feature per run. **Do not start F7 remainder until Gate F6 PASS.**

## Current state
| Item | Status |
|---|---|
| V0–V1, F1, F7, F2, F3, F4, F5, **F6** | Shipped — **Gate F6 pending** |
| Demo | Live: `.\demo\run_demo.ps1 -Fast` |
| After F6 PASS | F7 remainder (RBAC/RLS/rate limit) |

## Startup checklist
- [ ] `git checkout dms-v2 && git pull`
- [ ] `pip install -e ".[dev,api,dms]"`
- [ ] `pytest -q` — expect **145 passed, 4 skipped**

## Build loop
1. Gate F6 only — no new features until supervisor PASS
2. If fixing F6 blockers, stay in F6 scope
3. `pytest -q` green
4. Append `CHANGELOG_DMS.md` on fixes
5. Update `STATUS.md` + `python scripts/handoff.py --write`
6. Paste `CLAUDE_HANDOFF.md` to Claude → wait for PASS
7. **Stop** until gate clears

## Demo
```powershell
.\demo\run_demo.ps1 -Fast
```
Skills admin: http://localhost:3000/skills

## Ponytail
`PONYTAIL_DEFAULT_MODE=full` — see `docs/PONYTAIL.md`
