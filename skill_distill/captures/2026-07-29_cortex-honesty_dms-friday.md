---
id: 2026-07-29_cortex-honesty_dms-friday
source: manual
date: 2026-07-29
operator: cursor-agent
prompt_used: parent product-status ask + explore lanes
distill_trace: skill_distill/DISTILL.md
status: normalized
lanes: [dms_friday, engine_honesty, product_roles]
agents:
  - 80a11a5f-3ec6-4c00-92bf-749900ad38ff
  - 651e2a21-df1b-4046-a570-36a3f3c126b7
---

## Raw answer

Merged honesty from two explore lanes (2026-07-29):

### Product / engine
- Cortex = orchestration brain; AirGPT = shell; OpenVault = keys/gate.
- Netie Clicks/Pointer: Cortex sidecar + telemetry client — not documented as “Pointer”/“Space” in-repo.
- Multi-agent: 4 workflow templates + race/OSR; default single/minimal; no agent teams.
- JEPA/collapse = cosine on 64-dim feature-hash — **not trained**.
- MemPalace / Mem0 / Qdrant memory plane: **not shipped** (RawKnn + InMemory only). User “all done” claim = false.
- Model routing: T0–T3 + provider_order + bakeoff shipped; per-role verifier ranking not landed.
- Sidecar `/dms/secure*` only when `PACK=dms` (config default `ruma` → 404 risk).

### DMS Friday
- Green: L0/L1 over `data/dms_demo.duckdb`, 36/36 golden, ~64.7% paraphrase, 0 wrong.
- Gaps: lakehouse empty + disconnected from Q&A; query_skill 42/0; no Excel-copilot product; L2 unwired.
- Friday P0: lakehouse migrate + wire Q&A; provenance API/UI; paraphrase close; upload-xlsx→ask smoke.

## Extracted facts

| Fact | Evidence | Confidence | Promote |
|------|----------|------------|---------|
| MemPalace/Mem0/Qdrant memory not shipped | memory/stores, G1 checklist | high | rule |
| JEPA is proxy cosine not trained WM | action_value.py, scoreboard | high | rule |
| Lakehouse zero tables; Q&A uses demo DuckDB | STATUS, FOUNDATION_AUDIT | high | parking |
| PACK≠dms drops Netie sidecar routes | app.py pack gate | high | rule |
| Friday ROI = migrate+wire+provenance+paraphrase | DMS Friday explore | high | parking |

## Action YAML

```yaml
- id: friday-dms-lakehouse-wire
  promote: parking
  action: Run lakehouse_migrate; point warehouse_db/semantic at silver; prove /dms/query reads lake.
  condition: Friday DMS demo
  distill: skill_distill/captures/2026-07-29_cortex-honesty_dms-friday.md

- id: friday-dms-provenance-ui
  promote: parking
  action: Extend DMSQueryResponse + demo UI with layer/badge/suggestions.
  condition: Friday DMS demo
  distill: skill_distill/captures/2026-07-29_cortex-honesty_dms-friday.md

- id: honesty-memory-jepa
  promote: rule
  action: Never claim MemPalace/Mem0/Qdrant memory or trained JEPA as shipped.
  distill: skill_distill/captures/2026-07-29_cortex-honesty_dms-friday.md

- id: pack-dms-sidecar
  promote: rule
  action: Demo/Pointer requires PACK=dms; verify /dms/secure before act demos.
  distill: skill_distill/captures/2026-07-29_cortex-honesty_dms-friday.md
```

## Netie implications

- Build now (Friday): lakehouse wire, provenance, paraphrase, xlsx→ask smoke
- Park: trained JEPA, MemPalace/Mem0, Pointer verifier rewrite, per-role model bakeoff
- Tests: bench.accuracy + bench.paraphrase; lakehouse_status non-empty after migrate

## Citations

- distill: skill_distill/captures/2026-07-29_cortex-honesty_dms-friday.md
- docs/dms/FOUNDATION_AUDIT_2026-07-27.md
- PRODUCT_ROLES.md
