# C2 classification -- DMS evacuation boundary (before file moves)

This table is the pre-move inventory requested in C2. **No file moves are done in this step.**
Read and review this table before letting any code move.

| Module / path | Class | Reasoning |
|---|---|---|
| `CortexOS/dms/warehouse_db.py` | (a) generic lakehouse | Connection pool, semantic-layer loading, read-only helpers are engine/lake primitives. |
| `CortexOS/dms/sql_guardrail.py` | (a) generic lakehouse | Read-only SQL guardrail + limits are reusable engine safety. |
| `CortexOS/dms/profiler.py` | (a) generic capability | Runtime profiling utility, not domain-specific. |
| `CortexOS/dms/answer_engine.py` | (b) DMS product logic | Layered certified/metric/freeform routing is DMS vertical behavior. |
| `CortexOS/dms/query_service.py` | (b) DMS product logic | NL slot extraction and metric dispatch rules are DMS product. |
| `CortexOS/dms/cleaner.py` | (b) DMS product logic | Data-cleaning assumptions tied to DMS sample schema. |
| `CortexOS/dms/entry_analyser.py` | (b) DMS product logic | Domain entry mapping is vertical behavior. |
| `CortexOS/dms/seed_demo.py` | (c) demo scaffolding | Demo fixture seeding only. |
| `CortexOS/dms/generate_sample.py` | (c) demo scaffolding | Demo sample generation only. |
| `packs/dms/lakehouse/*` | (a) generic lakehouse | Catalog/tables wrappers are engine-level, vertical-neutral. |
| `packs/dms/security/*` | split (a)+(b) | Generic guards (PII, injection, auth) are engine; DMS policy constants stay product. |
| `packs/dms/audit/ledger.py` | (a) engine governance | F1 hash-chain ledger is a cross-vertical trust primitive. |
| `packs/dms/semantic/*` | (b) DMS product logic | Metric vocabulary, certified queries, and query skills are DMS semantic layer. |
| `packs/dms/vision/*` | (b) DMS product logic | Warehouse intake/movement/dimension flows are vertical. |
| `packs/dms/ingest/*` | (b) DMS product logic | DMS domain ingest contracts. |
| `packs/dms/pipelines/*` | (b) DMS product logic | DMS promote/pipeline expectations. |
| `packs/dms/streams/*` | split (a)+(b) | Stream buffer contract can be generic; DMS stream schemas stay product. |
| `packs/dms/agents/*` | split (a)+(b) | Generic agent runtime hooks move engine-side; DMS employee/detector logic stays product. |
| `packs/dms/classify/*` | (b) DMS product logic | Intent classification is DMS vertical. |
| `packs/dms/chat/*` | (b) DMS product logic | Chat threads are DMS product UX. |
| `packs/dms/generative/*` | (b) DMS product logic | Generative brain is DMS vertical. |
| `packs/dms/tasks/*` | (b) DMS product logic | Task suggest/gate/extract are DMS product. |
| `packs/dms/skills/*` | (b) DMS product logic | Skill capture is DMS vertical. |
| `packs/dms/ontology/*` | split (a)+(b) | Action-type registry is engine capability; DMS-specific types stay product. |
| `packs/dms/persona/*` | (b) DMS product logic | Persona profiles are DMS vertical. |
| `packs/dms/compliance/*` | split (a)+(b) | Compliance engine is generic; DMS-specific rulesets stay product. |
| `packs/dms/actions/*` | (b) DMS product logic | Export actions are DMS vertical. |
| `demo/dms-ui/*` | (c) demo scaffolding | UI belongs in `D:\DMS\apps\ui`; leave tombstone after move. |

## Boundary rules (implemented in `tests/contract/test_import_boundaries.py`)

1. `cortex_contract` package may not import `CortexOS`, `netie`, or `packs`.
2. `CortexOS/*` may not import `packs.*` (temporary allowlist for `engine_routes.py` while C2 evacuation executes).

## Next move step (not executed here)

1. Move (a) candidates into `CortexOS/lakehouse` + generic governance modules.
2. Export (b) + (c) to `D:\DMS` product tree.
3. Remove transition allowlist entry once C2 move is complete.
