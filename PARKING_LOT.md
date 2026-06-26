# PARKING_LOT.md
**Deferred ideas. Do not build until condition is met. Add new items here mid-sprint, not to active BUILD_PLAN.**

---

## P1 — Full Palantir ontology + AIP parity
Governed semantic objects, lineage, actions. **Condition:** 1+ paying clients, F1–F7 production-hardened.

## P2 — WASM / Firecracker production hardening
**Condition:** First enterprise client conversation.

## P3 — DAG token optimization + Temporal durable execution
**Condition:** 100+ DAG runs/day from real clients.

## P4 — respond.io-style endpoint / Closer auto-reply
WhatsApp/email in, sentiment score, warmed delayed draft (2–5s), human approve before send. **No em dash** in generated replies. **Condition:** DMS paying partner OR explicit RUMA priority.

## P5 — Full F2–F7 if pilot asks for chat loop before we planned
**Condition:** V0+V1 live + client confirms they want task-suggest chat next.

## P6 — SQL automation / CSV-Excel ingest pipeline
Folder watch, schema infer, agentic cleaning (AI proposes, deterministic applies), Splink dedup, standard output format, Claude export script.
**Condition:** V1 gated + pilot has dirty imports.
**Research when building:**
- great-expectations/great_expectations
- dbt-labs/dbt-core
- moj-analytical-services/splink
- cleanlab/cleanlab
- argilla-io/argilla
- GitHub search "agentic data cleaning" by stars at build time

## P7 — Annotation / labeling pipeline
YOLO / Grounding DINO / Label Studio self-hosted. **Condition:** 50+ real warehouse interactions from pilot.

## P8 — Automated handoff script (beyond manual STATUS paste)
**Condition:** Manual STATUS discipline breaks down. Until then: `python scripts/handoff.py`.

## P9 — Cortex as respond.io replacement (full)
**Condition:** DMS revenue + RUMA Closer validates sentiment draft loop.

## P10 — FDE full playbook
**Condition:** First paid pilot completes — write from what happened.

## P11 — Post-quantum crypto (ML-KEM / ML-DSA)
**Condition:** Regulated-industry client demands it.

## P12 — Company central brain (everyone contributes files)
**Condition:** F2–F6 live + 3+ regular users at one client.

## P13 — Blockchain / Web3 / Talkie / ASA / AIM / NetieX / Vanguard
**Condition:** H2, DMS profitable.

## P14 — Captured skills modify compliance gate thresholds
Let F6 learned skills change F5 YAML rules, value thresholds, or executable verdicts.
**Condition:** Separate architecture gate — violates current invariant (skills → suggest only; rules → gate).
**Never:** blur suggest ranking with compliance execution without explicit supervisor approval.

---

## Move out of parking lot
1. Condition met.
2. Claude gate or explicit decision.
3. Add to `docs/dms/BUILD_PLAN.md`.
4. Update `STATUS.md`.
Never mid-sprint.
