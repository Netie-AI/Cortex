# PARKING_LOT.md
**Deferred ideas. Do not build until condition is met. Add new items here mid-sprint, not to active BUILD_PLAN.**

---

## P1 — Full Palantir ontology + AIP parity
Governed semantic objects, lineage, actions. **Condition:** 1+ paying clients, F1–F7 production-hardened.

## P2 — WASM / Firecracker production hardening
**Condition:** First enterprise client conversation. *(Scaffold: `CortexOS/execution/wasm_isolate.py` — fuel sandbox only.)*

## P3 — DAG token optimization + Temporal durable execution
**Condition:** 100+ DAG runs/day from real clients. *(Partial: Ponytail middleware shipped — see `CortexOS/ponytail/` and `docs/PONYTAIL.md`. S1 watcher agents activate DBOS path per BUILD_PLAN_V2 — governed detect→draft→approve landed; durable resume still open.)*

## P4 — respond.io-style endpoint / Closer auto-reply
WhatsApp/email in, sentiment score, warmed delayed draft (2–5s), human approve before send. **No em dash** in generated replies. **Condition:** DMS paying partner OR explicit RUMA priority. *(Research: `docs/research/respond_io_analysis.md`.)*

## P5 — Full F2–F7 if pilot asks for chat loop before we planned
**Condition:** V0+V1 live + client confirms they want task-suggest chat next. *(Note: F2–F5 now shipped in codebase; pilot confirmation still required before sales promise.)*

## P6 — SQL automation / CSV-Excel ingest pipeline
Folder watch, schema infer, agentic cleaning (AI proposes, deterministic applies), Splink dedup, standard output format.
**Condition:** V1 gated + pilot has dirty imports.
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
**Condition:** F2–F6 live + 3+ regular users at one client.

## P13 — Blockchain / Web3 / Talkie / ASA / AIM / NetieX / Vanguard
**Condition:** H2, DMS profitable.

## P14 — Engine-as-SDK / company dual-brain
The whole runtime packaged as an engine + SDK an FDE points at a customer's data/use-case to generate a governed app. "Dual brain" = reasoning/runtime brain (`netie-engine`) + application/ontology brain (`main`), sharing one governance spine. This is the *how* under P1 (ontology parity) and P12 (company central brain). **Plan:** `docs/strategy/ENGINE_SDK_DUAL_BRAIN_PLAN_2026-07-23.md`. **Condition:** reconciliation option chosen (plan §2) + Ontology O1/O3/O4 shipped. *(The "SDK" is O4 `agents/sdk.py`; the "make the app you want" is O7 `scripts/new_pack.py`.)*

## P15 — netie-engine ↔ main capability landing
`main` and `netie-engine` diverged by 327 files / +34.5k / −5.2k (branched at `dms-v2`). **Do not big-bang merge/rebase** (aborts on add/add conflicts across `registry.py`/`store.py`/engine routes). Land engine capabilities (L0 DuckLake, rawknn/hybrid-RAG memory, Q1/Q2, S0 streams, agent orchestration) into `main` **one gate + one green CI at a time**; pick a canonical home per duplicated core file. **Condition:** plan §2 decision made. *(netie-engine stays the R&D feeder; see plan §2 option C→B.)*

## P16 — Agentic hardening from research
Fold in patterns from the Claude Code Ultimate Guide + Cursor changelog (July 2026): safety hooks (dangerous-action block → injection detect → output-secrets scan), Restrict/Allow/**Request** permission tiers, context-pressure thresholds, agent lifecycle hooks (`beforeSubmitPrompt`/`afterAgentResponse`/`stop`), an **evals harness** before the agent builder runs for a paying client, and an engine session/observability dashboard. **Condition:** before tool #2 / before the SDK loads any third-party MCP. *(Detail: plan §4.)*

## P17 — Cortex Engine API layer + self-host packaging (the external product surface)
Two ways for outside builders to consume Cortex (the final goal — `docs/strategy/CORTEX_FINAL_GOAL.md`): **(a) hosted API** to use only the parts they need (orchestration, retrieval, memory, model routing, ontology/action governance, context assembly); **(b) downloadable "netie engine"** to self-host, configure (models/storage/routing/governance), and run against their own data/base. Both hit the same governed core (actions-only write, RBAC + ledger). The Ontology Agent SDK (O4) is the in-process surface this wraps; the context-engineering API (shipped `be945ac`) is an early example of an engine surface to expose. **Condition:** O4 Agent SDK shipped (a stable in-process surface to wrap) + first external-consumer ask.

## P18 — Engine API documentation + whitepaper + architecture reference
Make the engine adoptable **without reading source**: strong per-surface API docs (contracts/examples/auth), the design **whitepaper** (ontology-as-memory + actions-as-only-write-path + dual-brain + hosted/self-host governance parity), and an architecture reference built on the O2 codebase map. First-class deliverables, not afterthoughts — the API *is* the product. **Condition:** P17 API layer has a stable surface to document.

---

## Move out of parking lot
1. Condition met.
2. Claude gate or explicit decision.
3. Add to `docs/dms/BUILD_PLAN.md`.
4. Update `STATUS.md`.
Never mid-sprint.
