# STATUS.md
**Last updated:** 2026-10-08 | **Gate:** G2.3 OSR SHIPPED | Rules and merge flow: `AGENTS.md`
**Rule:** Update after every gate. Read `CURSOR_HANDOFF.md` first. Leave next prompts in
`docs/dms/packets/NEXT_LANES.md`. Live work is GitHub issues; this file only points at
them. Nothing here is PASS or COMPLETE, and no line here is a GitHub CI claim.

## Live (open issues; last state recorded here)

| Area | Issues | Last recorded state |
|---|---|---|
| Brain FreeRoute | #350 | Writer lane: `/dms/brain` model calls go through `CortexOS.crew.freeroute.complete`. Named refusal `model_route_unavailable` when unarmed. Draft. Nothing PASS. |
| RUN-AUTH-01 spend on model run routes | #358 | Draft PR #362. Steward spends. Viewer `api_viewer` is a named 403 and sentinel 0 on the five run routes. `DMS_AUTH_DISABLED` still opens only `POST /api/engine/run` (parent already did). `/run`, workflow run/resume, and constructor `/run` do not honor the flag. Not a CI claim. |
| C7 L2 serve / retire cascade | #17 #104 #105 #288 | #104 part 2 merged `b6bd559`; G-sh NOT MET (no real shadow traffic). Phase 1b gate cases are hand-written same-shape stand-ins, unit evidence only. #288 Workday WRONG=1 open. |
| Contract-ask stamp | #289 | Merged; L2 copies only the actual FreeRoute RouteStamp, refuses `L2_ROUTE_STAMP_MISSING`. Draft evidence only. |
| FreeRoute / router / keys | #211 #212 #267 #272 | #211: no live Studio re-prove, armed live path unproven. #212: INCOMPLETE (fixture 40.00% and pinned-26 exact 0.00% are this-run only). #272: merged `27f79ea8`, local not proven; header/SSE copies of `served_local` PENDING. |
| Governed RAG | #33 #34 | RAG-02 PARTIAL (served path still `query_service.rag_answer` file scan). Do not read the 2026-08-25 close claim. |
| GOLD-01 | #13 #18 | Founder TTY, not an agent. |
| Liberty | #222-#225 | Proxy JEPA (cosine / `action_value`) only; no trained world model. |
| RSF | #151 #154 #155 #156 | COMPLETE needs an R-0003 different-run verify. |
| Crew export / desktop | #197-#200 | HOLD. |
| Image auth-off | #363 | IMAGE-AUTH-01 in draft. Images must not set `DMS_AUTH_DISABLED` or `CORTEX_DEV_MODE`. Startup refuses auth-off without dev mode. Auth tests refuse dev mode. Image-auth scan lists `git ls-files` only. A repo whose `git ls-files` fails stops with `GIT_LS_FILES_FAILED` and does not walk. Shipped Dockerfiles set `DMS_REFUSE_DEMO_KEYS=1`. Not a CI claim. Not PASS. |

## Standing constraints

- DMS #180 (gen 57.69% / exact 38.46%, WRONG=0 @ `d2f116a6`) is cited cross-system
  only: an offline `bind_plan` slot binder scored by a badge-only judge, not a model
  score. A fixture result is not like-with-like with the pinned 26 and never replaces it.
- `served_*` come from the served RouteStamp only, never inferred from the requested
  model. Masking-off measurements are never compared with masking-on.
- Never claim trained JEPA, MemPalace, Mem0 or Qdrant memory as shipped.
- Control is display-only (F-0030); Crew never POSTs run/goal/route/secrets to it.
- Cortex's own key is never lent to an HTTP caller; armed generate from a non-loopback
  peer without an `ov_` key is 401 (A-0009). HUMAN_STOP blocks live SSH and Cursor
  cloud spawn.
- App approval stays human. Learning never unlocks autonomy; `transfer_funds`,
  `send_message`, `publish`, `approve_app`, `deploy` never self-authorise. The `/fire`
  untrusted-payload wrap is mandatory. Ledger and telemetry hold identifiers, never prose.
- P16 evals harness (`agent_sdk/evals.py`) is a required gate before O6.
- Do not start G3.0 until P23 is promoted out of `PARKING_LOT.md`. G2.6 waits on
  OpenVault P17a.
- External `netie.bat` calls `START_ENGINE.bat`, never uvicorn directly.

## Test baseline

`python -m pytest tests/ -q` (the count lives in the last gate log, not this file;
local RLS skips without a DSN). `python -m scripts.secrets_scan` -> 0 findings.

## Handoff

North-star `docs/strategy/CORTEX_FINAL_GOAL.md` | G2 loop
`docs/strategy/ENTERPRISE_GEN_CFSM_LOOP_PLAN.md` (P21) | next prompts
`docs/dms/packets/NEXT_LANES.md` | truth map `docs/dms/TRUTH_GROUND_MAP.md` | research
`docs/research/findings/P0_INDEX.md` | context engineering `docs/CONTEXT_ENGINEERING.md`

## Archive (shipped; detail in git history and `CHANGELOG_DMS.md`)

- **2026-09 Crew / Insights / Liberty / RSF:** #116 Crew shell (memory API, policy
  ladder, belt claim, router harden, A2A, life, facts.md, assign, AppShell); #196
  Insights spine; #213 `/v1/insights`; scale/build (PR #210); #232 facts prove (live
  `:8020` was NOT_PROVEN); #269 ROUTER-1; #276 served passthrough (PR #286); RSF-04/06/07
  code landed (issues stay open above).
- **2026-09-04 wave 1:** auto-merge on public `main`; PRs #98 Crew belt, #106 C7-01,
  #108 EVAL-01, #111 CI hygiene, #107 SPACE-01; contract 1.2.0 release.
- **2026-08 engine + DMS handoff:** DAG ledger rows / cost API (#87); ANS-01..04,
  DOC-01, CONTRACT-01; unbound session abstains; refused route is `Badge.ABSTAIN`;
  ledger append verifies against the chain; CI billing block resolved.
- **2026-07 G1-G2.5:** G1 racing router, cFSM P0/P1, routines + governor, app package
  and importer; G2.0 EnterpriseGoal (non-removable ethical floor), G2.1 seeker (silence
  litmus), G2.2 action value + goal audit, G2.3 OSR, G2.4 ActionEvent telemetry, G2.5
  commitments. Flaky golden benchmark root cause: DuckDB exclusive read-write lock.
- **2026-07 O-series / E0 / CI:** O1-O5, O7 ontology + Agent SDK + sidecar + new-pack;
  Oracle-scale E0 A1-A6; L0 DuckLake reconcile; context engineering; Find Skills;
  skill_distill; F1-F7, V0-V1, Q1/Q2/L0-L2/S0/S1, F8 `export_pptx`; CI green 2026-07-22.
