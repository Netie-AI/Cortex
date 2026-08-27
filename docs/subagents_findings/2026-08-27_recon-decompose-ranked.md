# 2026-08-27 recon-decompose ranked work-list

PREFLIGHT: HIT on overnight RAM/worktree findings. MISS on this ranked list until now.
Survey: Claude Code workflow `cortex-recon-decompose` (wf_7e30789f-75b). Rank phase died on session limit. Ranked here from 70 raw / 43 startable items.

distill: skill_distill/captures/2026-08-27_claude-code_workflows-runtime.md

## House (do not violate)

Held PRs: Cortex #4, #41, #43, #44. Founder-blocked: #12, #13, #17, #18, #42. Never weaken `manifest.py`. Never add `_C2_ALLOWLIST` entries. Never hand-edit `contract/*.json`. GitHub Actions for Netie-AI is billing-blocked (checks fail in ~4s, zero steps) so red CI is not a code signal.

## File-ownership map (do not parallel-write)

| Owner | Files |
|-------|--------|
| badge-fail-closed | CortexOS/api/contract_routes.py, tests/dms/test_enrich_answer_provenance.py |
| dep-floors | pyproject.toml, tests/packaging/test_dependency_floors.py |
| lakehouse-connect-write | scripts/lakehouse_migrate.py |
| dedupe-chat | CortexOS/api/app.py, packs/dms/__init__.py, tests/packaging/test_profile_routes.py |
| p22-sdk (separate branch) | CortexOS/agent_sdk/sdk.py, backends.py |
| adv-bench (separate branch) | bench/adversarial.py |
| answer-path gates (separate branch) | tests/dms/test_q2_answer_engine.py, tests/test_dms/test_dms_pipeline.py, tests/dms/test_certified_synonyms.py, tests/dms/test_meta01_catalog.py |
| ledger-port (not this wave; collides sdk.py) | goal_audit.py, dag_runner.py, sdk.py, ontology/registry.py, tests/contract/test_import_boundaries.py |

## Ranked startable (value desc, risk asc)

1. badge-fail-closed -- unknown engine badge must not stamp SESSION (catalog/document/sql_not_analyzable/empty). F40 class. high/low
2. pyproject floors -- delete dead poetry caret table; raise litellm/cryptography/pillow/starlette/fastapi. high/low
3. lakehouse connect_write -- scripts/lakehouse_migrate.py raw duckdb.connect is a live RW/RO collision. high/low
4. p22 govern SDK reads -- query_objects bypasses enforce_manifest. high/medium (other branch)
5. adv-bench vacuous negatives -- SKU-BETA class. high/medium (other branch)
6. answer-path gate repairs -- q2 zero-rows, low-stock tautology, catalog plan-only. high/low (other branch)
7. Netie-KB index date -- real red CI (other repo)
8. Pointer #29 HUD report -- unstarted (other repo)
9. docs honesty -- WASM row, contract surface, STATUS double header, test baseline >=330
10. dedupe chat route registration -- spec-neutral, collision-free vs PR #44
11. OpenVault #38/#39 verify-and-close
12. DMS vendored OpenAPI 1.2.0 stale vs Cortex main

## Blocked (keep, do not start)

SEC-01 residual / query_service legacy -- PR #44. EVAL-01 corpus modules -- PR #4. C7 -- founder #12/#17. SPACE-01 -- #42. transformers floor -- [full] extras + RAM. ledger get-entry -- contract MINOR. landing FTP secrets -- founder. Space SEC-01 -- founder product call.

## Merge park

PR #71 (EPIC-002 wheel test) MERGEABLE but UNSTABLE: org Actions billing-blocked, auto-merge skipped. Local claim: 3 passed. Do not squash-merge against merge-when-perfect until billing lifts or a human admin-merges.
PR #70 (night_shift WIP, +1782) MERGEABLE UNSTABLE -- too large to treat as "perfect". Do not merge from this wave.
