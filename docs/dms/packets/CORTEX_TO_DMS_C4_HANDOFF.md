# Cortex → DMS — C4-min live flip handoff

**Date:** 2026-07-30  
**Status:** C4-min implemented on Cortex; ready for DMS `DMS_ASK_MODE=live` after one bind→ask smoke.

Binding detail: [`CORTEX_TO_DMS_C4_SUBMIT.md`](CORTEX_TO_DMS_C4_SUBMIT.md).

## What landed on Cortex

| Piece | Where |
|-------|--------|
| `POST /v1/contract/submit` (bind + SQL) | `CortexOS/api/contract_routes.py` → `CortexOS/execution/submit.py` |
| Session bind registry | `CortexOS/execution/session_manifests.py` |
| One read pool | `CortexOS/execution/pool.py` |
| DuckDB opens | `CortexOS/execution/warehouse.py` only (under `CortexOS/`) |
| JWKS at startup | `CortexOS/db/lifespan.py` |
| `ask` fails closed if unbound | HTTP 409 `session_unbound` |
| `_true_count` on live path | through `execute_count` / `enforce_manifest` |
| AST invariant | `tests/contract/test_duckdb_location.py` |

## DMS flip (minimal)

1. Pin `cortex-contract==1.1.0`; client from `openapi-1.1.0.json`.
2. OpenVault up; Cortex cold-start JWKS refresh (or warm `CORTEX_JWKS_CACHE`).
3. Mint → `submit` `plan.kind=session_bind` → `ask` with same `session_id`.
4. Manifest `row_predicates` must allow every warehouse table the answer SQL touches (keys are the table allowlist).
5. Set `DMS_ASK_MODE=live`. Keep demo badges non-certified.

## Next Cortex lane

**C6 shipped** — see [`CORTEX_TO_DMS_C6_KICKOFF.md`](CORTEX_TO_DMS_C6_KICKOFF.md).
C4.follow items remain in PARKING_LOT **P22** (lakehouse duckdb migrate, multi-pool, F5 contract minor).
