# 2026-08-27 recon-decompose ranked work-list

PREFLIGHT: HIT. Survey: Claude Code workflow `cortex-recon-decompose` (wf_7e30789f-75b). Rank died on session limit; the 43 startable items were recovered from the 11 survey StructuredOutputs (70 raw).

distill: skill_distill/captures/2026-08-27_claude-code_workflows-runtime.md

## House (do not violate)

Held PRs: Cortex #4, #41, #43, #44. Founder-blocked: #12, #13, #17, #18, #42. Never weaken `manifest.py`. Never add `_C2_ALLOWLIST` entries. Never hand-edit `contract/*.json`. GitHub Actions for Netie-AI is billing-blocked (checks fail in ~2-4s, zero steps) so red CI is not a code signal. Do not merge Dependabot majors that bump demo/dms-ui to Next 16 or React 19.

## Landed (this fleet, origin/main)

| Item | Where |
|------|--------|
| badge-unknown-defaults-confident / f40-unknown-badge | Cortex #73 |
| pyproject-single-dependency-table / dep-floor-regression-gate | Cortex #73 |
| lakehouse-sync-connect-write | Cortex #73 |
| dedupe-chat-route-registration | Cortex #73 |
| recon/adversarial/ticket/build workflow templates | Cortex #73 |
| p22-govern-agent-sdk-reads | Cortex #75 |
| adv-bench-vacuous-negative-assertions | Cortex #72 |
| q2-exclusion / pipeline-lowstock / certified-catalog | Cortex #74 |
| epic002-release-ships-unverified-wheel | Cortex #71 |
| contributing-sources / contract-ask grounding / handoff / changelog / dependabot / product-roles | Cortex #76 |
| netie-kb-index-date-nondeterminism | Netie-KB #1 |
| dms-59-ff03 / dms-resync-stale-vendored-contract | dms #100 |
| pointer-hud-report-control | Pointer #38 |
| openvault-stale-tickets-38-39-verify | OpenVault #45 |
| ledger-port-evacuate-four (goal_audit, dag_runner, sdk; registry still held) | Cortex wave2 C2 branch |
| pointer-word-sink-independent-verify | Pointer main `test/invariants/word-sink.test.js` PASS 2026-08-28 |

## Ranked remaining startable (value desc, risk asc)

1. c2-gate-blind-to-nested-imports -- AST vs grimp; CLAUDE.md names the weaker gate. Protected. high/medium
2. dms-ui-next-15-5-21-and-overrides -- `npm install --package-lock-only` only; do not merge Dependabot Next 16 / React 19. high/medium
3. move-api-auth-to-engine -- C2 evacuate. high/medium
4. harden-allowlist-to-paths -- basename `registry.py` is too wide. Protected. high/low
5. architecture-truth-pass -- WASM row still in ARCHITECTURE.md. high/low
6. warehouse-ephemeral-attach-helper -- catalog.py in-memory duckdb + ATTACH. medium/medium
7. p22-corpus-cases-sdk-read-shape -- add hostile cases only, never reclassify. medium/low
8. epic001-one-documented-start-command -- README :8000 vs :8010 leftover. medium/low
9. meta01-close-or-carry-envelope-half -- envelope fields still missing on some paths. medium/low
10. move-crypto-to-engine / move-seed-demo-to-pack -- C2 remaining. medium/low
11. dms-contract-pin-gate-cannot-detect-staleness -- dms repo. medium/low
12. netie-kb-generated-rules-dead-drive-path -- Netie-KB. medium/low
13. dms-ui-next-14-2-35-fallback -- only if Next 15 is refused. medium/low
14. netie-control-no-ci-workflow -- low
15. ci-doctor-no-readme-no-ci -- low

## Parked against held PRs (were startable in survey, not startable now)

- accuracy-gate-can-fail-proof -- PR #4 edits `bench/accuracy.py`
- status-refresh -- PR #4 edits STATUS.md
- ontology/registry ledger-port -- PR #4 edits `CortexOS/ontology/registry.py`

## Blocked (keep, do not start)

SEC-01 residual / query_service legacy -- PR #44. EVAL-01 corpus modules -- PR #4. C7 -- founder #12/#17. SPACE-01 -- #42. transformers floor -- [full] extras + RAM. ledger get-entry -- contract MINOR. landing FTP secrets -- founder. Space SEC-01 -- founder product call.

## Merge park

PR #70 (night_shift WIP, +1782) -- too large. Do not merge.
Dependabot #77-#81 (swr / react 19 / recharts 3 / next 16) -- do not merge; majors, RAM, live demo.
Held drafts #4 / #43 / #44 stay held.
