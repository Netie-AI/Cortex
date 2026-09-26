# EPIC-TRUST-01: fail closed at the edges a buyer or operator actually touches

Repo /home/user/Cortex, branch claude/cortex-scale-agi-state-fkg2b6 at e2ad43c (VERIFIED: `git log --oneline -1`).

## Problem

Discovery found six places where Cortex reports success, or accepts a caller, when it should refuse.

1. **Anyone who can reach the port can run code.** `/api/apps/*` and `/api/connectors/*` have no auth dependency. The chain import, then approve, then start runs the manifest `commands` (CortexOS/api/app_routes.py:61-80, CortexOS/execution/app_store.py:298-328, 375-410; CortexOS/api/connector_routes.py:198+ uses an APIRouter with no dependencies). VERIFIED by reading. The discovery probe also returned 400 rather than 401 without a key, and 200 with host paths on GET /api/connectors/workspaces.
2. **A crashed engine still answers an unbound session.** `answer_question` catches every non-Manifest exception and falls back to `_answer_question_legacy`. That path ignores `require_grounding` and reads the demo warehouse (CortexOS/dms/query_service.py:945-950). The `grant_kind == 'none'` guard at :933-934 only runs on the success path. VERIFIED by reading; the discovery lane also ran it and got 1000 real rows with badge=None.
3. **An abstaining document answer still lists warehouse tables as sources.** `_is_abstain_signal` looks only at raw tokens (CortexOS/api/contract_routes.py:119-129). An unmapped badge such as `document` maps to Badge.ABSTAIN (:155), but contributing_sources is still filled from granted tables (:216-219). VERIFIED by reading.
4. **Workflow runs orphaned by a restart stay "running" and cannot be resumed.** `resume()` refuses any run whose persisted status is `running` (CortexOS/execution/workflow_runner.py:507-510). Nothing reaps orphaned rows, and `_ensure_loop` (:41-57) never reconciles them. VERIFIED by reading.
5. **The C-SEC-2 RLS deny proof can pass with zero tests run.** The step at .github/workflows/rls.yml:46-47 has no skip guard, and the module skips entirely when DMS_LEDGER_DSN is empty (tests/dms/test_rls_blocks_out_of_scope_read.py:24-27). The multi-process step at rls.yml:79-82 is also unguarded. VERIFIED by reading.
6. **The EXIF-GPS privacy test never runs.** It needs `piexif`, which is in no extra (tests/dms/test_v0_warehouse.py:30-31, 88-98). VERIFIED by reading. The discovery lane saw it skip locally.

## Buyer-visible outcome

- An operator exposing the engine on a LAN cannot have a stranger install and start an app. Without a key, every mutating app or connector route returns 401. A viewer key gets 403 on those routes.
- A customer never gets warehouse numbers when the engine fails for an unbound session. They get an explicit abstain.
- An ABSTAIN envelope never lists warehouse tables as its sources, and never carries a drillthrough token.
- After a crash, a workflow run shows as `error (interrupted, resumable)` and Resume works without a manual cancel.
- The RLS and ledger security proofs fail when their tests skip, and the GPS-stripping privacy control is tested on every CI run.

## Non-goals

- Auth on the routine, workflow, goal, dms_query, chat, warehouse, task and contract planes. These are follow-up epics that reuse the port from T1.
- Changing get_caller fail-open semantics (demo keys, DMS_AUTH_DISABLED).
- Zip-bomb limits.
- Any contract/ or packages/cortex_contract change.
- route_to_metric / C7-06.
- Any CortexOS/crew file.
- The RAG stub relevance matcher (it shares query_service.py with T2).
- Postgres ledger loop affinity.

## Tickets (parallel; write targets are pairwise disjoint)

| ID | Title | Severity | Write targets |
|---|---|---|---|
| TRUST-01 | Auth port and role gates for /api/apps and /api/connectors | critical | CortexOS/security/auth_port.py (new), packs/dms/security/api_auth.py, CortexOS/api/app_routes.py, CortexOS/api/connector_routes.py, tests/test_connectors/test_connector_routes.py, tests/dms/test_app_onboarding.py, tests/dms/test_apps_importer.py, tests/test_api_security/test_app_connector_auth.py (new) |
| TRUST-02 | Engine-failure fallback abstains when grounding is required | critical | CortexOS/dms/query_service.py, tests/dms/test_answer_fallback_grounding.py (new) |
| TRUST-03 | ABSTAIN provenance clears sources and drillthrough | medium | CortexOS/api/contract_routes.py, tests/dms/test_enrich_abstain_sources.py (new) |
| TRUST-04 | Reap orphaned workflow runs on loop start | high | CortexOS/execution/workflow_store.py, CortexOS/execution/workflow_runner.py, tests/test_execution/test_orphan_run_reaper.py (new) |
| TRUST-05 | Junit all-passed guard for every RLS proof step | high | scripts/check_junit_all_passed.py (new), tests/packaging/test_check_junit_all_passed.py (new), .github/workflows/rls.yml |
| TRUST-06 | Run the EXIF-GPS privacy test without piexif | high | tests/dms/test_v0_warehouse.py, tests/security/test_photo_sanitize.py (new) |

No ticket depends on another. TRUST-01 adds the auth port as its own new file, which later auth epics will reuse.

## Risks

- **TRUST-01 and the C2 boundary.** app_routes.py and connector_routes.py are not in `_C2_ALLOWLIST` (tests/contract/test_import_boundaries.py:64-92, VERIFIED). A direct `from packs.dms.security.api_auth import ...` would fail the contract test. The ticket must use an engine-side port, the same pattern as CortexOS/audit/ledger_registry.py and CortexOS/security/redact_port.py, and must not add allowlist lines.
- **TRUST-01 and unauthenticated UI callers.** A desktop or crew UI that calls /api/apps with no key would break. A grep of the main tree found no html/js caller of /api/apps or /api/connectors; only tests and feature_stubs.py matched (VERIFIED). Desktop lanes #197-#200 are ASSUMED not to call them.
- **TRUST-02 and the demo bridge.** Tightening the fallback could remove demo answers that silently relied on it. The legacy path stays when require_grounding=False.
- **TRUST-03 and DMS.** DMS may currently read contributing_sources on `document` answers. Clearing them changes values, not the schema, so no contract bump is needed.
- **TRUST-04 in multi-process deploys.** A second engine process sharing the same wf_runs DB could reap a sibling's live run. The ticket scopes reaping to rows whose started_at is earlier than the reaping process's start time.
- **TRUST-05.** The check must itself be able to fail; its unit tests prove this.

## Verified vs assumed

- VERIFIED (read in this pass): packs/dms/security/api_auth.py:42-45,103-115; CortexOS/api/app.py:25-45; CortexOS/api/app_routes.py:61-80; CortexOS/api/connector_routes.py:198-210; tests/contract/test_import_boundaries.py:52-92; CortexOS/dms/query_service.py:920-950; CortexOS/api/contract_routes.py:119-129,150-158,210-235; CortexOS/execution/workflow_runner.py:41-57,500-530; CortexOS/execution/workflow_store.py:271-273,484-491; .github/workflows/rls.yml:1-3,44-48,79-82; tests/dms/test_rls_blocks_out_of_scope_read.py:22-27; tests/dms/test_v0_warehouse.py:25-35,88-99. Also: `freeroute` appears in CortexOS/crew/runtime.py, server.py and insights.py.
- VERIFIED by the discovery lanes (they ran these; not rerun here): the /api/apps probe returning 400 instead of 401; the legacy fallback returning 1000 rows; the piexif and RLS skips appearing in a local run.
- ASSUMED: CI skips the EXIF test (inferred from the ci.yml install line). No production UI caller of /api/apps exists outside this checkout. app_store.approve and start behave as the discovery lane described at app_store.py:298-410 (not reread in this pass).


## Coordinator notes (2026-09-24, before build)

- Default auth is fail-open: with `DMS_API_KEYS` unset, the demo keys published in the repo (including `admin:dms-demo-admin-key`) are accepted. TRUST-01 closes the unauthenticated path for any deployment that sets real keys, but it does not change the default. Flipping the default (API-AUTH-05) changes deploy behaviour and Dockerfiles and stays a founder decision.
- No UI in the repo calls `/api/apps` or `/api/connectors` (grep over html/js/ts/tsx/py outside tests), and the UIs that call gated routes already send `X-API-Key`, so gating does not block legitimate local use.
- Queued next (they depend on TRUST-01's auth port): API-AUTH-02 (routine/workflow/goal/race/activity/dag), API-AUTH-03 (dms_query/chat/warehouse/task, and `approved_by` spoofing), API-AUTH-04 (contract ledger/append; needs founder), API-HARDEN-06 (zip limits).
