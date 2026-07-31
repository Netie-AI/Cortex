# Netie Cortex — Documentation Index

**Start here:** [`ACTIVE.md`](ACTIVE.md) — **engine-first**, all consumers.  
DMS lane only: [`dms/ACTIVE.md`](dms/ACTIVE.md).  
Archived PASS/stale packets: [`bin/README.md`](bin/README.md).

## Active (few files)

| Doc | Purpose |
|-----|---------|
| [ACTIVE.md](ACTIVE.md) | **Engine map** — Cortex + siblings; repo policy |
| [engine/CONSUMERS.md](engine/CONSUMERS.md) | DMS · AirGPT · Pointer · OpenVault · packs |
| [../PRODUCT_ROLES.md](../PRODUCT_ROLES.md) | Brains vs keys vs shell vs Act |
| [strategy/CORTEX_FINAL_GOAL.md](strategy/CORTEX_FINAL_GOAL.md) | Engine north star |
| [strategy/CORTEX_WHITEPAPER.md](strategy/CORTEX_WHITEPAPER.md) | Architecture thesis + ecosystem (P18) |
| [dms/packets/NEXT_LANES.md](dms/packets/NEXT_LANES.md) | Always-continue lane prompts |
| [dms/ACTIVE.md](dms/ACTIVE.md) | DMS/Spaces/eval lane only |
| [dms/SANDBOX_ORIENTATION.md](dms/SANDBOX_ORIENTATION.md) | Host-shim · Docker · Spaces · Act |

## Strategy (reference)

| Doc | Purpose |
|-----|---------|
| [strategy/NETIE_CORTEX_MASTER_PLAN.md](strategy/NETIE_CORTEX_MASTER_PLAN.md) | Horizons / kill-list (phase stamp may lag STATUS) |
| [strategy/ENTERPRISE_GEN_CFSM_LOOP_PLAN.md](strategy/ENTERPRISE_GEN_CFSM_LOOP_PLAN.md) | G2 / P21 proactive loop |
| [strategy/ENGINE_SDK_DUAL_BRAIN_PLAN_2026-07-23.md](strategy/ENGINE_SDK_DUAL_BRAIN_PLAN_2026-07-23.md) | Dual-brain land-one-gate (O1–O7 largely shipped) |
| [strategy/DMS_C2_CLASSIFICATION_2026-07-29.md](strategy/DMS_C2_CLASSIFICATION_2026-07-29.md) | C2 boundary inventory |

RUMA vertical: archived → [`bin/verticals/RUMA_PHASE3_5.md`](bin/verticals/RUMA_PHASE3_5.md) (parked until DMS pays).

## DMS reference (not daily)

| Doc | Purpose |
|-----|---------|
| [dms/SUPERVISOR_GATE.md](dms/SUPERVISOR_GATE.md) | Milestone paste template |
| [dms/TRUTH_GROUND_MAP.md](dms/TRUTH_GROUND_MAP.md) | Feature→file→test |
| [dms/DMS_EVAL_AND_STRESS_PLAN.md](dms/DMS_EVAL_AND_STRESS_PLAN.md) | Eval / envelope honesty |
| [dms/BUILD_PLAN_V2_LAKEHOUSE.md](dms/BUILD_PLAN_V2_LAKEHOUSE.md) | Lakehouse master plan |
| [dms/BUILD_PLAN.md](dms/BUILD_PLAN.md) | Historical F1–F7 (shipped) |
| [dms/POSITIONING.md](dms/POSITIONING.md) | Wedge / FDE framing |
| [dms/VISION_GOVERNANCE.md](dms/VISION_GOVERNANCE.md) | V0–V3 governance |

## Archive & bin

| Path | Purpose |
|------|---------|
| [archive/](archive/) | Legacy architecture extracts |
| [bin/](bin/) | Done/pass/superseded packets + subagent results |

## Handoff (repo root)

| Doc | Purpose |
|-----|---------|
| [../STATUS.md](../STATUS.md) | Current gate, debt, next feature |
| [../CONTEXT.md](../CONTEXT.md) | Paste into new Claude chat |
| [../ARCHITECTURE.md](../ARCHITECTURE.md) | Built vs partial inventory |
| [../PARKING_LOT.md](../PARKING_LOT.md) | Deferred ideas only |

`python scripts/handoff.py` prints a clipboard-ready block.

## Cursor governance

- `.cursor/rules/` · `.cursor/skills/` · `.cursor/AGENTS.md`
- `CHANGELOG_DMS.md` — per-feature ship log

## Run

```powershell
pip install -e ".[dev,api,dms]"
.\demo\run_demo.ps1
python -m pytest tests/ -q
```
