# STATUS.md
**Last updated:** 2026-07-03 | **Gate:** F5 shipped → **Gate F5 pending (Claude)**
**Rule:** Update after every gate. Read `CURSOR_HANDOFF.md` first.

---

## Current state at a glance

| Layer | Status | Gate |
|---|---|---|
| F1–F4, Ponytail, Brain, security harness | Shipped | PASS |
| **F5 compliance gate** | **Shipped** | Gate F5 pending |
| Demo (`run_demo.ps1`) | **Live-ready** | Verified |
| F6 skill capture | Planned | After Gate F5 |
| Phase 0 deploy | Planned | Parallel-plan only |
| Local GPU inference (Qwen opt-in) | Script ready | Manual |

## Test baseline
```
pytest -q → 141 passed, 4 skipped
```

## Demo (show immediately)
```powershell
.\demo\run_demo.ps1 -Fast
```
Guide: `docs/DEMO.md` — Query · Warehouse · Chat · Data · Audit

## Active feature
**Gate F5** — paste `CLAUDE_HANDOFF.md` + `docs/dms/GATE_F5_PACKET.md` to Claude.

## Next three moves
1. Claude verifies Gate F5
2. After PASS → F6 skill capture
3. Phase 0 deploy planning (parallel OK)

## Handoff
- **Claude:** `CLAUDE_HANDOFF.md`
- **Spec:** `docs/dms/F5_PLAN.md`, `docs/dms/GATE_F5_PACKET.md`
