# Multi-PR post-billing CI gate audit

| Field | Value |
|-------|-------|
| Date | 2026-08-28 |
| Keywords | cicd, billing, pr-85, pr-84, pr-82, pr-70, dependabot, public-repo |
| Main idea | Post-billing code gate: #85/#84/#82/#70 PASS; Dependabot React19/Next16/recharts3 FAIL. Unblock = billing OR make public (README claims open source). |

Cite: distill prior `2026-08-28_cicd-billing-gate-fail.md`.

## Unblock paths (either)

1. https://github.com/organizations/Netie-AI/settings/billing — fix payment / raise spending limit
2. Make `Netie-AI/Cortex` **public** (repo description already says open source) — free Actions minutes. Agent token is 403 admin; human must flip visibility.

## Code verdicts (ignore billing)

| PR | Verdict | Notes |
|----|---------|-------|
| #85 C2 exact-path | PASS | On main tip; INVARIANT-CHANGE present; merge first |
| #84 dms-ui CVE | PASS | next 14.2.35; SWC skew mitigated by Babel; rebase 1 behind |
| #82 wheel verify | PASS | CORTEX_CONTRACT_WHEEL workflow-inline, not secret |
| #70 night_shift WIP | PASS | No new packs imports; rebase INDEX.md; merge last |
| #77 swr 2.5.1 | PASS | Optional |
| #78 react 19 | FAIL | ERESOLVE vs next 14 — close |
| #79 recharts 3 | FAIL | next build breaks — close |
| #80 next 16 | FAIL | major migration — close |
| #81 react-dom 19 | FAIL | same as #78 — close |

## Merge order after Actions runs

#85 → #82 → #84 → #70. Close #78–#81.

## #86 local mirror

All required checks PASS locally including rls-proof on Postgres 16.
