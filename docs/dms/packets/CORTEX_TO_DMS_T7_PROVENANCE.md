# Cortex → DMS — T7 provenance spine (Cortex half)

**Date:** 2026-07-30  
**From:** Cortex engine lane (T7-min)  
**To:** DMS lane (bronze ingest already flat-column)  
**Status:** binding for Sources / drill-through. Contract **1.2.0** (additive minor).

DMS shipped bronze `_src_row` / `_src_ref_id` / `_ingest_id` + `dms.source_ref`.  
Cortex ships pipeline lineage gate, drill-through SQL rewrite, HMAC token, and  
additive Answer fields. Architecture Appendix A STRUCT `_src[]` is a later migrate.

---

## 1. Column contract (demo-core)

| Column | Meaning |
|--------|---------|
| `_src_ref_id` | UUID → `dms.source_ref.id` |
| `_src_row` | 1-based row in the source member |
| `_ingest_id` | FK to ingest ledger / ingest_run |

Future STRUCT `_src STRUCT(ref_id, row)[]` remains the join-native shape; flat  
columns are the T7-min wire both sides agree on today.

---

## 2. Contract version

Pin **`cortex-contract==1.2.0`**. Regenerate client from  
`contract/openapi-1.2.0.json`. Keep serving **1.1.0** (ignore new fields/ops).

Do **not** reimplement `canonical_manifest_bytes`.

### Additive Answer fields (1.2.0)

| Field | Notes |
|-------|--------|
| `answer_id` | Stable id for this answer |
| `assumptions` | `list[str]` (Provenance.assumptions string still present) |
| `contributing_sources` | Cards for Sources panel (may be empty until silver propagates) |
| `drillthrough_token` | HMAC; required for `POST /v1/contract/drillthrough` |

### New operation

`POST /v1/contract/drillthrough` — body `{ "token": "…" }` only. Never raw SQL.

---

## 3. Drill-through sequence

```
1. Live bind → ask (C4) under a signed session manifest
2. Answer may include drillthrough_token when sql_used is present
3. POST /v1/contract/drillthrough { "token": "<drillthrough_token>" }
4. Cortex rewrites answer SQL → contributing rows + provenance columns
5. Executes under the SAME session VerifiedManifest (C4 registry)
```

Refusals: unbound/expired session, bad/expired token, manifest hash mismatch →  
fail closed (no ACL bypass via click).

---

## 4. Pipeline lineage (Cortex packs)

Silver pipeline YAML must declare:

```yaml
lineage: propagate   # or aggregate
lineage_reason: "…"  # required when aggregate
```

`propagate` runs fail if the target lacks provenance columns. Until transforms  
project `_src_*`, defs use `lineage: aggregate` with an honest reason.

---

## 5. DMS expectations

1. Keep attaching flat provenance at bronze ingest (already done).
2. Pin contract 1.2.0; regenerate client; ignore nothing that is additive.
3. Sources panel: render `contributing_sources` when non-empty; call drillthrough  
   with the token only.
4. Do not invent drill-through SQL client-side.

---

## 6. Out of scope (later)

- STRUCT `_src[]` join propagate (C9-full)
- Plausibility stage / sellable C10 (needs C8 `query_run`)
- Full §4.7 `values[].id` click tokenization in UI
