---
id: 2026-07-29_pointer-demo_dms-lake-map
source: manual
date: 2026-07-29
operator: cursor-agent
prompt_used: parent demo-build ask + explore lanes
distill_trace: skill_distill/DISTILL.md
status: normalized
lanes: [pointer_demo, dms_lake, repo_map]
agents:
  - 38d4efcf-c1cb-487f-b5dc-37e41c5aec85
  - 5026f43e-374e-424a-9593-ffe8f8ea7bbb
plan: .cursor/plans/pointer_dms_demo_build_ae7eef9e.plan.md
---

## Raw answer

### Pointer (`D:\Netie Clicks`, package `netie-pointer`)

- Start: Cortex `start_cortex_engine.ps1 -Port 8010 -Pack dms` → OpenVault OpenMW console `:5000` → `npm start`
- Act fail-closed on `/dms/secure`; Ask uses OpenVault vision (fail-open)
- Top live fixes: soften pngFingerprint verify for type/fill; clear coords after recapture so targeting re-aims; pre-plan capture; demo cortexKey; recipe coords + `press` in needsFreshView; README Cortex step
- Recipes: Excel-shaped only; no Word new-doc recipe

### DMS lake + Space

- Excel swamp = medallion bronze→silver→gold (not a named “swamp” module)
- L0–L2 APIs shipped; lake empty until migrate; Q2 still on `dms_demo.duckdb`
- Catalog UI = build `demo/dms-ui/app/studio/` (U0) — **not** Netie Space
- Netie Space = Quick Look preview; optional Cortex RAG only
- AirGPT autospawns Cortex via `cortex_client.py` → same start script, `PACK=dms`

## Extracted facts

| Fact | Evidence | Confidence | Promote |
|------|----------|------------|---------|
| Pointer Act requires Cortex 8010 PACK=dms | ecosystem failClosed | high | rule |
| SHA-256 verify aborts type plans | main.js verifier | high | skill |
| Recapture wasted if planner coords kept | targeting.js early return | high | skill |
| Studio catalog not built; Space ≠ lake UI | dms-ui, Netie Space brief | high | parking |
| lakehouse_migrate seeds bronze/silver/gold | scripts/lakehouse_migrate.py | high | parking |

## Action YAML

```yaml
- id: pointer-demo-fixes
  promote: skill
  action: Implement Phase A fixes in D:\Netie Clicks per plan
  distill: skill_distill/captures/2026-07-29_pointer-demo_dms-lake-map.md

- id: dms-studio-u0
  promote: parking
  action: demo/dms-ui studio + migrate + ingest; Claude Code on Cortex
  condition: after Pointer demo green today
  distill: skill_distill/captures/2026-07-29_pointer-demo_dms-lake-map.md
```

## Netie implications

- Build now: Pointer A0–A2 (Cursor in Netie Clicks)
- Park/week: DMS Studio + Q2→silver (Claude Code in Cortex)
- Never: put lake catalog in Netie Space; claim trained JEPA this week

## Citations

- distill: skill_distill/captures/2026-07-29_pointer-demo_dms-lake-map.md
- plan: pointer_dms_demo_build
