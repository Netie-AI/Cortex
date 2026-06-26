<!-- generated 2026-06-26 -->
# CURSOR_HANDOFF — Builder Session Startup
**Read this first.** Then `STATUS.md` → `docs/dms/PHASE0_PLAN.md`.

---

## Your role
Builder. One increment per run.

## Current state
| Item | Status |
|---|---|
| F1–F6 | Shipped, gates PASS |
| F7 core | PASS — SOPS/rate-limit debt parallel |
| **Phase 0 deploy** | **Planning approved → ship P0.1 next** |

## Startup
```powershell
pip install -e ".[dev,api,dms]"
pytest tests/ -q   # expect 145 passed, 4 skipped
```

## Active spec
`docs/dms/PHASE0_PLAN.md` — start with **P0.1 env contract**

## Demo
```powershell
.\demo\run_demo.ps1
```
