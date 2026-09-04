# SPACE-01 space grant

keywords: SPACE-01, space_id, SessionManifestRegistry, SpaceUnbound, resolve_product_grant, grant, manifest
main_idea: A named Space is grounded only by that Space's signed bound manifest. Registry looks up (session_id, space_id) with no session-wide fallback. Engine never mints a grant.

## Assumptions (do not re-litigate)

1. A Space grants documents AND tables: docs via space_id-scoped retrieval; tables via sources in the Space's signed manifest (`row_predicates` keys).
2. Entitlement is decided by the signer (DMS) BEFORE minting. Engine enforces that the signed bound manifest names the Space the turn asks for. `Manifest.space_id` already exists. No wire/contract Pydantic change.
3. Space grant is signed and bound through the existing registry. One authority.

## What shipped

- `SessionManifestRegistry` keeps `_by_session[session_id]` as latest and indexes `_by_space[(session_id, space_id)]` when the bound manifest has a non-empty `space_id`.
- `resolve(..., space_id=)` looks up only that pair. Absent -> `SpaceUnbound` (code `space_unbound`, message names the Space). Expired -> evict + `SessionExpired`. Never fall back to the session-wide binding.
- `resolve_product_grant(..., space_id=)` refuses a verified grant whose `manifest.space_id` does not match. Registry resolve threads `space_id`.
- `_abstain_unbound` names the Space and tells the customer to select a Space they are entitled to (which binds its signed grant). No demo questions.
- `/v1/contract/ask` resolves with `body.space_id` and maps `SpaceUnbound` to HTTP 409. `/dms/query` and MCP thread optional `space_id` (not a frozen contract field).

## Reuse

- `tests/dms/test_unbound_session_abstain.py` Ed25519 bind helper
- `CortexOS/execution/session_manifests.py` `SessionUnbound`
- `packages/cortex_contract/execution.py` `Manifest.space_id`

cite: issue Netie-AI/Cortex#42
