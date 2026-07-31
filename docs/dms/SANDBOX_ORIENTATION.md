# Sandbox & execution orientation (2026-07-31)

**Canonical pointer:** also summarized in `docs/dms/ACTIVE.md`.

## What we run today

1. **Governed host tools (F8 slice)** — `CortexOS/execution/tool_runner.py`  
   Allowlist (ontology action types) → compliance → sanitize → write under `outputs/<actor>/<run_id>/` → F1 ledger.  
   First tool: `export_pptx` (host shim, not WASM).  
   Used by **any** pack/consumer that issues `TOOL_CALL` — not DMS-only.

2. **Docker for packaging / deploy** — `Dockerfile.core` / `Dockerfile.full`, app `dockerize()`, compose for Postgres/Qdrant.  
   This is **appliance / app isolation**, not the TOOL_CALL sandbox. AirGPT/OpenVault may ship their own compose; Cortex images stay engine API.

3. **Spaces “sandbox”** — DMS product meaning: ACL ∩ selected sources for chat/retrieve/amend (`DMS_SPACES_PRODUCT`). Data-plane enforce, not a VM.

4. **Act / `computer_control` (Pointer)** — peer consumer at `D:\Netie Clicks` hits Cortex `:8010` Act fail-closed.  
   Different isolation story from Spaces ACL and from WASM. Do not collapse “Pointer out of DMS demo” into “Act removed from engine.”

## What we are *not* doing now

| Idea | Status |
|------|--------|
| Production WASM / WASI / Firecracker | **P2 parked** — `wasm_isolate.py` is fuel scaffold for tests |
| Activepieces / RUMA Flows | **Binned** — `docs/bin/verticals/RUMA_PHASE3_5.md`; `activeflow/` optional delete |
| Third orchestrator / Temporal | Parked until run volume (P3) |

## Rule before tool #2

Low-risk host-shim OK if allowlist + compliance + ledger + output dir hold.  
Raise to stronger isolation only when risk warrants — and that work opens **P2**, not a silent rewrite of F8 docs.
