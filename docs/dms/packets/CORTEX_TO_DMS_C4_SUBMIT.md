# Cortex → DMS — C4 submit seam (live ask flip)

**Date:** 2026-07-30
**From:** Cortex engine lane (C4-min)
**To:** DMS lane (demo-core live flip / T2 already unblocked)
**Status:** binding for `DMS_ASK_MODE=live`. Demo mode does not need this packet.

C3 shipped verification and `enforce_manifest`. C4 wires them into one governed
`submit()` path, binds a verified manifest to a session, and requires that bind
before contract `ask`. No contract major: shapes stay on **1.1.0**.

---

## 1. Contract version

Still **1.1.0**. Regenerate `packages/cortex_client` from
`contract/openapi-1.1.0.json` only — never from `openapi-1.0.0.json`.

Do **not** reimplement `canonical_manifest_bytes`. Pin `cortex-contract==1.1.0`
and call the function. See [CORTEX_TO_DMS_C3_MANIFEST_CONTRACT.md](CORTEX_TO_DMS_C3_MANIFEST_CONTRACT.md).

`AskRequest` is unchanged (no `manifest` field). The manifest reaches Cortex via
`submit`, then `ask` resolves it by `session_id`.

---

## 2. Live sequence

```
1. Mint Manifest (DMS) — pool_id, issuer_key_id, issued_at, expires_at,
   allowed_paths, row_predicates, signature
2. POST /v1/contract/submit
     {
       "pool": {"id": "<same as manifest.pool_id>", "class_name": "default", "max_concurrency": 1},
       "plan": {"kind": "session_bind"},
       "body": {},
       "manifest": { ... }
     }
3. POST /v1/contract/ask
     {"question": "...", "session_id": "<manifest.session_id>", "space_id": optional}
4. Optional later submits with plan.kind == "sql" and body.sql for drill-through /
   certified SQL under the same session binding
```

`session_bind` verifies the manifest, checks `pool.id == manifest.pool_id`,
registers a `VerifiedManifest` for `session_id` until `expires_at`, and returns
`QueryResult(ok=true, status="bound", run_id=...)` with no warehouse open.

A SQL submit (`plan.kind == "sql"`, `body.sql` set) also refreshes the binding
and executes under `enforce_manifest`.

**Unbound `ask` fails closed** — Cortex does not invent a demo default session
on the live path.

---

## 3. Refusal / status codes (additive to C3)

| code / status | when | DMS action |
|---|---|---|
| (C3 codes) | signature, expiry, path, statement, analyzability | as C3 packet |
| `pool_mismatch` | `SubmitRequest.pool.id != manifest.pool_id` | fix mint / pool |
| `pool_required` | missing `manifest.pool_id` | mint with pool |
| `pool_saturated` | read-pool concurrency exhausted / queue timeout | retry once, then fail |
| `session_unbound` | `ask` with no prior successful bind for `session_id` | call submit bind first |
| `session_expired` | binding past `expires_at` | re-mint + re-bind |
| `sql_required` | `plan.kind=sql` without `body.sql` | fix client |

HTTP mapping for contract routes: security / policy refusals → **403** with
`detail.code`; saturation → **429**; unbound ask → **409**; malformed request →
**400**. Successful bind/SQL → **200** `QueryResult`.

---

## 4. Env / deployment (Cortex host)

| Variable | Purpose | Default |
|---|---|---|
| `OPENVAULT_URL` / `OPENVAULT_BASE_URL` | JWKS + key custody | `http://127.0.0.1:5000` |
| `CORTEX_JWKS_CACHE` | on-disk JWKS cache path | `data/engine/openvault_jwks.json` |
| `CORTEX_READ_POOL_ID` | sole read pool id for personal test | `default` |
| `CORTEX_READ_POOL_CONCURRENCY` | semaphore size | `4` |
| `CORTEX_READ_POOL_QUEUE_TIMEOUT_S` | wait for a slot | `5` |
| `CORTEX_STATEMENT_TIMEOUT_S` | per-statement cap (best-effort) | `30` |
| `DMS_WAREHOUSE_DB` | serving DuckDB path | `data/dms_demo.duckdb` |
| `DMS_READ_ONLY_QUERIES` | prefer read-only warehouse opens | off |

JWKS is refreshed at API startup (cold path). Hot-path verify never hits the
network. If OpenVault is down at boot, cached keys on disk still work; a fresh
issuer unknown to the cache fails as `manifest_unknown_issuer` until refresh
succeeds.

---

## 5. F5 compliance gate

F5 remains on **`/dms/tasks/gate/check`** and **`/dms/tasks/gate/acknowledge`** —
not on the 1.1 contract allowlist. Mutations still fail closed when Cortex is
down. A future contract **minor** may expose gate; do not invent a shadow client
API.

---

## 6. Demo vs live badges

`DMS_ASK_MODE=demo` may keep server-side scenario envelopes for UI stress tests.
Those badges must **not** claim certified / governed green. Only live Cortex
`Answer.provenance.badge` values from a real ask may show certified/governed.

---

## 7. Explicitly not in C4-min

- Multi-pool memory broker / write pool / T10 activation UI
- Full lakehouse pack DuckDB migration (tracked as C4.follow)
- Contract bump for `AskRequest.manifest` or F5 operationIds
- C6 scope-tagged memory (next Cortex lane after C4-min is green)
- C10 adversarial harness

---

## 8. Flip checklist

1. Cortex + OpenVault up; JWKS reachable or cache warm.
2. DMS pins `cortex-contract==1.1.0`; client from `openapi-1.1.0.json`.
3. Mint → `submit` bind → `ask` against `http://127.0.0.1:8010`.
4. Confirm unbound ask returns `session_unbound`; tampered signature refused.
5. Set `DMS_ASK_MODE=live`.
