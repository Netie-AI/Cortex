# STATUS.md

**Last updated:** 2026-10-09 MYT | **Main:** `f04995bb` (#376 merged, lock-only)
Rules and merge flow: `AGENTS.md`. Live list: `TASK.md`. AI-first law: `CLAUDE.md` section 9.
This file records pointers. It does not certify a gate.

## Founder direction

AI-first, DB-GPT / Genie style. The model writes SQL from ontology, schema, and
verified-example context. Escalation ladder, in order: plan-then-solve,
self-correct, a stronger OpenVault tier, reconfirm. Direct abstain only for an
ungranted table or destructive SQL. Keyword cascades and word rules stay out.

## Open chain (one merge at a time)

Engine `2.5.0` and contract `1.4.0` are separate lines. #329 is the PR that
introduces contract `1.5.0`. Later PRs in this chain rebase onto it.

| Order | PR | Item |
|---|---|---|
| 1 | #329 | C-LOOP-A. Plan then SQL via OpenVault FreeRoute. Owns contract 1.5.0. |
| 2 | #351 | SCHEMA-CONTEXT. Prove pin `279cbd85` ignores DMS `schema_context`. A pin move needs evidence. Never tip-deploy prove. |
| 3 | #321 | VERIFIED-QUERY. Steward-confirmed examples at ask time. |
| 4 | #313 | C7-06. Retires the `route_to_metric` keyword cascade as the serve chooser. |

## Leftovers

| Item | What |
|---|---|
| #375 | Issue. `lock_images.py` hardcodes `--python-version 3.11` (follow-up to #360). |
| #377 | Issue. `check_supply_chain` should fail on unlisted image-only packages (follow-up to #376). |
| #383 | Issue. Drop unimported diskcache 5.6.3. |
| trust_remote_code | Named ticket. No issue number was on the repo at this rewrite. `trust_remote_code=True` remains in `CortexOS/nlp/local_inference.py` and `scripts/finetune_dms_tone.py`. |
| #378-#382 | Dependabot draft PRs (portalocker, nvidia-cusparse, nvidia-cufft, mpmath, dbos). |

## PRs on main that this file used to call drafts

The PRs merged. The issues were still open at this rewrite. A merged PR is not a closed issue.

| PR | Merged (MYT) | Issue still open |
|---|---|---|
| #376 LOCK-ALIGN-01 (lock-only; this main) | 2026-10-09 10:32 | -- |
| #352 BRAIN-FREEROUTE-01 | 2026-10-09 05:43 | #350 |
| #366 IMAGE-AUTH-01 | 2026-10-09 07:54 | #363 |
| #370 FREEROUTE-LEARN-SOFT-01 | 2026-10-09 08:44 | #369 |
| #360 LITELLM-SKEW-01 | 2026-10-09 09:04 | #354 |
| #362 RUN-AUTH-01 | 2026-10-09 09:21 | #358 |

## Test baseline

On `f04995bb`, CI job lint-type-test pytest reported `2752/10/4`: 2752 passed, 10 skipped, 4 xfailed. Local RLS skips without a DSN.

## Still open (prior record, not re-audited here)

| Area | Issues | Last recorded state |
|---|---|---|
| C7 L2 serve / cascade | #17 #104 #105 #288 | Retirement is #313 in the chain above. #104 part 2 merged `b6bd559`; G-sh was not met (no real shadow traffic). Phase 1b gate cases are hand-written same-shape stand-ins, unit evidence only. #288 stays open. |
| Contract-ask stamp | #289 | Merged; L2 copies only the actual FreeRoute RouteStamp, refuses `L2_ROUTE_STAMP_MISSING`. Draft evidence only. |
| FreeRoute / router / keys | #211 #212 #267 #272 | #211: no live Studio re-prove, armed live path unproven. #212: INCOMPLETE (fixture 40.00% and pinned-26 exact 0.00% are this-run only). #272: merged `27f79ea8`, local not proven; header/SSE copies of `served_local` PENDING. |
| Governed RAG | #33 #34 | RAG-02 PARTIAL (served path still `query_service.rag_answer` file scan). Do not read the 2026-08-25 close claim. |
| GOLD-01 | #13 #18 | Founder TTY, not an agent. |
| Liberty | #222-#225 | Proxy JEPA (cosine / `action_value`) only; no trained world model. |
| RSF | #151 #154 #155 #156 | A different run still has to verify. |
| Crew export / desktop | #197-#200 | HOLD. |

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

## Handoff

North-star `docs/strategy/CORTEX_FINAL_GOAL.md` | G2 loop
`docs/strategy/ENTERPRISE_GEN_CFSM_LOOP_PLAN.md` (P21) | next prompts
`docs/dms/packets/NEXT_LANES.md` | truth map `docs/dms/TRUTH_GROUND_MAP.md` | research
`docs/research/findings/P0_INDEX.md` | context engineering `docs/CONTEXT_ENGINEERING.md`

History of older waves is in git and `CHANGELOG_DMS.md`. `docs/archive/task.md` does not bind.
