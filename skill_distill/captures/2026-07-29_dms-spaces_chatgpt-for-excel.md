---
id: 2026-07-29_dms-spaces_chatgpt-for-excel
source: manual
date: 2026-07-29
operator: cursor
prompt_used: skill_distill/prompts/MASTER_INTERROGATION.md
distill_trace: skill_distill/DISTILL.md
status: normalized
---

# DMS Spaces — ChatGPT for Excel/DB + sandbox scopes

## Raw answer

Owner direction + architecture critique (Snowflake/Databricks four layers, DuckLake≈Snowflake shape, 500GB vs 100TB, validation-time NL→SQL gate, rights in data plane, build order Phase0 Postgres before amend, no Excel write-back, 128GB hardware, pitch). Product: central chat + Spaces sandboxes over few DBs/files; personal/team/share; harder ~3GB mixed scenarios; Pointer external; focus DMS warehouse AI agents.

## Extracted facts

| Fact | Evidence | Confidence | Promote |
|------|----------|------------|---------|
| DuckLake catalog-in-SQL + Parquet ≈ Snowflake FoundationDB split | architecture note | high | parking |
| Space = ACL ∩ source set; retrieval must be data-plane scoped | owner + note | high | skill/rule |
| Excel write-back forbidden; export only | note §5 | high | rule |
| Phase0 Postgres ops/ledger before amend loop | note §5 | high | parking/build |
| 0 confidently wrong stays invariant; move gate to validation | note §3 | high | rule |
| Pointer out of DMS demo focus | owner | high | none |
| Thousands of connectors not shipped | honesty | high | none |

## Action YAML

```yaml
- promote: parking
  id: P12
  note: Extend company brain with Spaces sandboxes + central chat ACL
  distill: skill_distill/captures/2026-07-29_dms-spaces_chatgpt-for-excel.md
- promote: rule
  text: DMS Space chat may only retrieve/amend sources in space.sources; Excel is ingest-only (export for outbound)
  distill: skill_distill/captures/2026-07-29_dms-spaces_chatgpt-for-excel.md
```

## Netie implications

- Build now: doc locked at `docs/strategy/DMS_SPACES_PRODUCT_2026-07-29.md`; Phase0 Postgres when pilot starts
- Park: full connector marketplace; brokers; 100TB analytics design
- Tests required: Space isolation (~3GB mixed), confirm token versioning, BM25 part numbers

## Citations

- distill: skill_distill/captures/2026-07-29_dms-spaces_chatgpt-for-excel.md
- docs/strategy/DMS_SPACES_PRODUCT_2026-07-29.md
