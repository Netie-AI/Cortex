# PARKING_LOT.md
**Deferred ideas. Do not build until condition is met. Add new items here mid-sprint, not to active BUILD_PLAN.**

> **Owner decision 2026-07-20:** P3, P6, P12 activated (and P1 partially, O1–O3 plumbing only)
> into `docs/dms/BUILD_PLAN_V2_LAKEHOUSE.md`. Dispositions per item below and in that plan §8.
> All other conditions unchanged.

---

## P1 — Full Palantir ontology + AIP parity
Governed semantic objects, lineage, actions. **Condition:** 1+ paying clients, F1–F7 production-hardened.
*(Partial activation 2026-07-20: O1–O3 internal plumbing only, per `docs/ontology/CORTEX_ONTOLOGY_PLAN.md` §0 — customer-facing claims still gated.)*

## P2 — WASM / Firecracker production hardening
**Condition:** First enterprise client conversation. *(Scaffold: `CortexOS/execution/wasm_isolate.py` — fuel sandbox only.)*

## P3 — DAG token optimization + Temporal durable execution
**ACTIVATED 2026-07-20** → BUILD_PLAN_V2 Feature S1: DBOS Transact durable workflows (Temporal = documented scale-out path, not built). *(Partial: Ponytail middleware shipped — see `CortexOS/ponytail/` and `docs/PONYTAIL.md`.)*

## P4 — respond.io-style endpoint / Closer auto-reply
WhatsApp/email in, sentiment score, warmed delayed draft (2–5s), human approve before send. **No em dash** in generated replies. **Condition:** DMS paying partner OR explicit RUMA priority. *(Research: `docs/research/respond_io_analysis.md`.)*

## P5 — Full F2–F7 if pilot asks for chat loop before we planned
**Condition:** V0+V1 live + client confirms they want task-suggest chat next. *(Note: F2–F5 now shipped in codebase; pilot confirmation still required before sales promise.)*

## P6 — SQL automation / CSV-Excel ingest pipeline
Folder watch, schema infer, agentic cleaning (AI proposes, deterministic applies), Splink dedup, standard output format.
**ACTIVATED 2026-07-20** → BUILD_PLAN_V2 Features L1 (Auto-Loader-analog ingest) + L2 (declarative pipelines + expectations). Splink stays a later toggle.
**Research when building:**
- great-expectations/great_expectations
- dbt-labs/dbt-core
- moj-analytical-services/splink
- cleanlab/cleanlab

## P7 — Annotation / labeling pipeline
YOLO / Grounding DINO / Label Studio self-hosted. **Condition:** 50+ real warehouse interactions from pilot.

## P8 — Automated handoff ~~(beyond manual STATUS paste)~~
**SHIPPED 2026-06-26** — `CLAUDE_HANDOFF.md`, `CURSOR_HANDOFF.md`, `python scripts/handoff.py --write`. Keep this entry as historical; do not re-build.

## P9 — Cortex as respond.io replacement (full)
**Condition:** DMS revenue + RUMA Closer validates sentiment draft loop.

## P10 — FDE full playbook
**Condition:** First paid pilot completes — write from what happened.

## P11 — Post-quantum crypto (ML-KEM / ML-DSA)
**Condition:** Regulated-industry client demands it.

## P12 — Company central brain (everyone contributes files)
**ACTIVATED 2026-07-20** → BUILD_PLAN_V2 Feature U1 (wave 3): role-labelled shared memory scope + RLS on the netie.memory store.

## P13 — Blockchain / Web3 / Talkie / ASA / AIM / NetieX / Vanguard
**Condition:** H2, DMS profitable.

---

## Move out of parking lot
1. Condition met.
2. Claude gate or explicit decision.
3. Add to `docs/dms/BUILD_PLAN.md`.
4. Update `STATUS.md`.
Never mid-sprint.
