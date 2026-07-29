# Netie Cortex — Documentation Index

## DMS (current product focus)

| Doc | Purpose |
|-----|---------|
| [dms/BUILD_PLAN.md](dms/BUILD_PLAN.md) | F1–F7 feature sequence with Cursor prompts |
| [dms/POSITIONING.md](dms/POSITIONING.md) | Wedge strategy, competitive map, FDE playbook |
| [dms/VISION_GOVERNANCE.md](dms/VISION_GOVERNANCE.md) | V0–V3 warehouse vision + Claude gate checkpoints |
| [dms/SUPERVISOR_GATE.md](dms/SUPERVISOR_GATE.md) | What to paste Claude at each milestone |

## Strategy

| Doc | Purpose |
|-----|---------|
| [strategy/CORTEX_WHITEPAPER.md](strategy/CORTEX_WHITEPAPER.md) | **Canonical** architecture thesis, apps, roadmap, branch map (P18) |
| [strategy/CORTEX_FINAL_GOAL.md](strategy/CORTEX_FINAL_GOAL.md) | North star: best engine |
| [strategy/DMS_SPACES_PRODUCT_2026-07-29.md](strategy/DMS_SPACES_PRODUCT_2026-07-29.md) | DMS Spaces / ChatGPT-for-Excel product lock |
| [strategy/NETIE_CORTEX_MASTER_PLAN.md](strategy/NETIE_CORTEX_MASTER_PLAN.md) | Horizons, revenue path, scope kill-list |
| [strategy/RUMA_PHASE3_5.md](strategy/RUMA_PHASE3_5.md) | RUMA vertical (parked until DMS pays) |

## Archive

Legacy agent task lists and architecture extracts — reference only, not active build input.

## Handoff (repo root — read before every session)

| Doc | Purpose |
|-----|---------|
| [../STATUS.md](../STATUS.md) | Current gate, debt, next feature |
| [../CONTEXT.md](../CONTEXT.md) | Paste into new Claude chat |
| [../ARCHITECTURE.md](../ARCHITECTURE.md) | Honest built vs partial inventory |
| [../PARKING_LOT.md](../PARKING_LOT.md) | Deferred ideas only |

`python scripts/handoff.py` prints a clipboard-ready block.

## Cursor governance (repo root)

- `.cursor/rules/` — always-on and file-scoped agent rules
- `.cursor/skills/` — DMS ship, gate verify, subagent dispatch
- `.cursor/AGENTS.md` — subagent roles and sequencing
- `CHANGELOG_DMS.md` — per-feature ship log (agents append here)

## Run

```powershell
pip install -e ".[dev,api,dms]"
.\demo\run_demo.ps1
```

# Tests
python -m pytest tests/test_dms/ -q
python -m pytest tests/ -q
```
