# Cortex → DMS — C7 kickoff (schema gate)

**Status:** C7-full **shipping** — schema retrieval + FreeRoute SQL generation + literal
normalization feeding the C7-min EXPLAIN/retry gate.  
**Shipped earlier (C7-min):** EXPLAIN dry-run + retry structure on submit/L2 path.

Do not weaken `manifest.py` refusals.  
Files: `CortexOS/dms/sql_validate_gate.py`, `packs/dms/generative/schema_retrieval.py`,
`packs/dms/generative/sql_generator.py`, `packs/dms/generative/literal_normalize.py`,
`packs/dms/generative/promotion.py`, `CortexOS/dms/answer_engine.py`.
