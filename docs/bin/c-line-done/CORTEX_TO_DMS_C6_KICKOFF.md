# Cortex → DMS — C6 scope-tagged memory

**Date:** 2026-07-30  
**Status:** implemented on Cortex (engine lane). OpenVault trust root already settled (`30a8d9a`).  
**Architecture:** DMS_TECHNICAL_ARCHITECTURE §11 / §15 — C6 required for D1; do this **before** DMS T3 Spaces chat relies on shared memory.

## Invariant

Every memory entry carries `entry_scope` (tags that wrote it). Retrieval keeps only rows where:

```text
entry_scope ⊆ session_scope
```

**in the storage query** (SQLite `NOT EXISTS` on `scope_tags` for rawknn; candidate filter before score for in-memory). Never rank forbidden rows then drop them in Python.

## What landed

| Piece | Where |
|-------|--------|
| Scope helpers | `CortexOS/memory/scope.py` |
| `MemoryRecord.entry_scope` | `CortexOS/memory/store.py` |
| SQL subset filter | `CortexOS/memory/stores/rawknn.py` (`scope_tags`) |
| Session anaphora Space key | `CortexOS/dms/answer_engine.py` (`session_id::space:…`) |
| Contract ask passes `space_id` | `CortexOS/api/contract_routes.py` |
| API `entry_scope` / `session_scope` | `CortexOS/api/memory_routes.py` |

## DMS expectation (when T3 starts)

1. Chat sends `session_id` **and** `space_id` on `/v1/contract/ask`.
2. Memory upserts tag `space:<id>` (and `tenant:<id>` when known).
3. Memory queries pass `session_scope` = caller's Space membership tags — never post-filter hits in the app.
4. Follow-up anaphora in Space B must not see Space A's prior SQL (engine already keys on Space).

## Exit criteria

1. Cross-Space vector query returns no foreign entries.
2. Wider entry (`{space:a, space:b}`) is invisible to a session that only has `space:a`.
3. Same `session_id`, different `space_id` → isolated prior SQL.
4. OpenVault JWKS path unchanged (C3/C4); C6 does not touch custody.
