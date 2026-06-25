<!-- generated 2026-06-25T17:03:05+00:00 -->
<!-- generated 2026-06-25T17:01:35+00:00 -->
# CURSOR_HANDOFF — Builder Session Startup
**Read this file first.** Then `STATUS.md` → `CONTEXT.md` → `ARCHITECTURE.md` → `PARKING_LOT.md`.

---

## Your role
You are the **builder**. One feature per run. Sequential only for dependent features.

## Authority order (when docs conflict)
1. `STATUS.md` Active feature / Next three moves **wins**
2. `CHANGELOG_DMS.md` — what's actually shipped
3. Linear sequence F1→F7→V0→V1→V2→V3 (with approved V-first wedge)

## Current state
| Item | Status |
|---|---|
| V0 warehouse | Shipped, gate PASS |
| V1 dimensioning | Shipped, **Gate V1 pending** |
| F1 ledger | Shipped (SQLite + optional Postgres DSN) |
| F7 security | Shipped (PII, crypto, RLS SQL) |
| F2 chat | Shipped (minimal foundation) |
| Active | Run gate verify → then F3 classify OR Phase 0 deploy |

## Startup checklist
- [ ] Read files above
- [ ] `pip install -e ".[dev,api,dms]"`
- [ ] `pytest -q` baseline green
- [ ] Identify ONE feature from STATUS — do not scope-creep

## Build loop (mandatory)
1. Plan → smallest diff
2. Implement + tests
3. `pytest -q` green
4. Append `CHANGELOG_DMS.md`
5. Update `STATUS.md` + regenerate handoffs: `python scripts/handoff.py --write`
6. Run gate skill → paste `CLAUDE_HANDOFF.md` to Claude
7. **Stop** — do not start next feature until gate PASS

## Skills
| Need | Invoke |
|---|---|
| Ship feature | `Use dms-subagent-dispatch` |
| Gate packet | `Use dms-claude-gate` |
| Research only | Task `explore` (parallel OK) |

## Ponytail (token reduction)
- Default mode: **full** (YAGNI, stdlib-first)
- Before merge: run `ponytail-review` mindset — delete before add
- Config: `PONYTAIL_DEFAULT_MODE=full` or `~/.config/ponytail/config.json`
- Install: `/plugin marketplace add DietrichGebert/ponytail` (Claude Code)

## Parallel allowed
- Read-only explore subagents
- Phase 0 deploy **planning** parallel with F1/F7 (not parallel code writes on same files)

## Anti-scope (law)
- No rip-and-replace WMS
- No PII in prompts without `redact_for_prompt`
- No BIG_API in hot loops
- No auto-commit vision below confidence + F5 gate
- Sacred tests: add new, never weaken

## FastAPI
No `from __future__ import annotations` in route modules. Pydantic at module level.

## Demo
```powershell
pip install -e ".[dev,api,dms]"
.\demo\run_demo.ps1
```
