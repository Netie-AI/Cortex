# Cortex → DMS — C8 kickoff (durable query_run)

**Status:** parked after C5-min.  
**Slice:** persist submit/ask runs from `CortexOS/execution/telemetry.py` to SQLite/Postgres `query_run` (not ring buffer only). Unblocks C10 plausibility.

Architecture §5.2 / §5.4. No contract major required for engine-local persistence.
