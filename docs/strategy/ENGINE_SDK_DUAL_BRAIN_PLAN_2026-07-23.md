# Engine-as-SDK / Company Dual-Brain — Implementation Plan

**Date:** 2026-07-23 · **Author of record:** owner + Claude (Opus 4.8 planning pass) · **To be implemented by:** a later Fable 5 session.
**Status: PLAN ONLY.** Nothing here is built. Do not present anything below as shipped.

Read first: `ARCHITECTURE.md`, `CONTEXT.md`, `STATUS.md`, `PARKING_LOT.md`,
`docs/ontology/CORTEX_ONTOLOGY_PLAN.md` (the O0–O8 phases this plan sits on top of),
`docs/ontology/PALANTIR_AIP_RESEARCH.md` (the 7 portable ideas).

---

## 0. North star (one line)

> **The whole Cortex runtime is an engine + SDK that a company (or an FDE) points at their data and use-case to generate the governed app they actually want — "company dual brain": a reasoning/runtime brain and an application/ontology brain, sharing one governance spine.**

This is the same throughline as the world-engine brief
(`docs/dms/packets/CLAUDE_CODE_WORLD_ENGINE_BRIEF_2026-07-22.md`): *ontology-as-memory + LLM-as-reasoner + actions as the only write path*. This doc is the concrete build/reconciliation plan under that north star.

> **Sharpened 2026-07-23 — see `docs/strategy/CORTEX_FINAL_GOAL.md`:** Cortex's final goal is to be **the best engine** — we improve only orchestration + engine capability; verticals (DMS, etc.) are *consumers*. The engine reaches outside builders two ways: a **hosted API** (use only the parts you need) and a **downloadable/self-host "netie engine"** (configure + run on your own base). Made adoptable by first-class **API docs + a whitepaper** (PARKING_LOT P17/P18). In this plan's terms: **Brain B (`netie-engine`) is the product; Brain A (`main`/DMS) is the first reference consumer.**

---

## 1. The "dual brain", concretely — and the git reality

The two brains already exist as two branches (post branch-consolidation, 2026-07-23):

| | Brain A — **Application / Ontology** | Brain B — **Engine / Runtime** |
|---|---|---|
| Branch | `main` (`origin/main`) | `netie-engine` (`origin/netie-engine`) |
| Holds | DMS vertical app; governance spine **F1 ledger / F5 compliance / F7 RBAC**; security (reversible PII, token vault, WASM honesty, Postgres RLS); CI-green (Test + Secrets Scan + RLS Proof) | Engine-up runtime: hybrid RAG + rawknn mmap store, **DuckLake lakehouse (L0)**, memory store, semantic/answer engine (Q1/Q2), streams (S0), agent orchestration, model/tier routing, AirGPT sidecar |
| Role in the vision | The **first app** built on the engine + the governance every app inherits | The **SDK/runtime** companies build their apps on — the "boost for agentic + LLM use" |
| Maturity | Production-track, gated, CI-green | R&D; ahead on engine capability, behind on governance/CI |

**Git reality (measured 2026-07-23):** `main` and `netie-engine` differ by **327 files / +34,554 / −5,160 lines**. They branched at the `dms-v2` base (`d86a3e0`) and evolved in parallel. The core engine files are already a shared *snapshot* on both (`CortexOS/engine/registry.py` 186↔186, `CortexOS/memory/store.py` 121↔121), but as whole trees they have massively diverged. A blind `git rebase netie-engine` onto `main` aborts on the **first** commit with add/add conflicts on `registry.py`, `store.py`, `engine_routes.py`, `memory_routes.py` — i.e. both brains independently built the same components. That is an architecture reconciliation, not a mechanical rebase. **The rebase was attempted and safely aborted; `netie-engine` is untouched at `028fbfb`.** Archive tag `archive/dms-f6-phase0` preserves the old F6 branch.

---

## 2. THE gating decision — how to reconcile the two brains

> **✅ DECISION (2026-07-23, owner): Option B — shared engine core, keep dual brains — reached via path C (incremental capability landing). Option A (big-bang merge/rebase) is rejected.** This is settled; the plan below and the Fable 5 prompts (`docs/strategy/FABLE5_HANDOFF_PROMPTS.md`) execute it.

| Option | What it is | Cost | When it's right |
|---|---|---|---|
| **A — Big-bang merge/rebase** | Rebase or merge all 7 `netie-engine` commits onto `main`, resolving hundreds of conflicts with per-component "which wins" decisions | High + risky; can silently corrupt either brain; loses CI-green certainty for a long window | Only if you want a single trunk and are prepared to re-verify everything |
| **B — Shared engine core, keep dual brains** ⭐ | Pick a canonical home per engine component (runtime→`netie-engine`, governance/app→`main`); extract the shared core into one place both consume; reconcile only the *duplicated* core files | Medium; incremental; matches the "engine as SDK" north star exactly | **Recommended** — the dual brain is a feature, not debt |
| **C — Incremental capability landing** | Treat `netie-engine` as the R&D source and `main` as the integration trunk. Land engine capabilities into `main` **one gate at a time**, each behind its own CI-green proof (the repo's existing "ship one, prove it, then the next" discipline) | Low per step; slow overall; no grand unification event | The pragmatic path to reach **B** without a risky big-bang |

**Recommendation: C evolving toward B.** Reject A. Concretely:
1. **Never** land the whole `netie-engine` tree at once. Cherry-pick by *capability* (L0 lakehouse, rawknn memory store, Q1 semantic, Q2 answer engine, S0 streams, agent orchestration), each as its own branch off `main`, each with its own gate + CI green before the next.
2. For each duplicated core file (`registry.py`, `store.py`, engine routes), decide the canonical version **once** and delete the other copy — do not carry two.
3. The end state is **B**: a `cortex_engine` core (from `netie-engine`) that `main`'s DMS app and future company apps import through the Agent SDK (§4). `netie-engine` then becomes either retired or the ongoing R&D feeder.

Full per-capability landing order goes in the roadmap (§6). **Decision made — proceed to W1.**

---

## 3. The mechanism already designed — Ontology O-series

The "SDK for companies to build the app they want" is **not new work to invent** — it is largely the existing `docs/ontology/CORTEX_ONTOLOGY_PLAN.md` (phases O0–O8), which already maps Palantir/Foundry's model onto this codebase. Do not duplicate it; this plan only names how it packages into the dual-brain/SDK story:

- **O1 Ontology registry** (`packs/<pack>/ontology/*.yaml` → compiled into the ops DB) = the *metadata-is-data* spine (Palantir idea #4). One schema drives UI + agent read/write scope + permission checks (idea #1).
- **O3 Action-type registry (= F8)** = *actions are the only write path*, identical for human and agent (ideas #2, #7). This is the governance every app inherits from Brain A.
- **O4 Agent SDK** (`packs/<pack>/agents/sdk.py`: `list_object_types`, `list_action_types`, `call_action`, `query_objects`) = **the blessed in-process surface** any agent runtime uses to reach data/actions through compliance + RBAC + ledger. **This is "the SDK."**
- **O7 New-pack generator** (`scripts/new_pack.py`) = **the FDE payoff**: takes an approved object/link/action-type trio and scaffolds a new vertical pack shaped like `packs/dms/`. **This is "companies create the apps they want."**

So: **Brain B (engine)** provides reasoning/retrieval/memory/orchestration; **Brain A (governance + O-series)** provides the ontology, the action-gated write path, and the SDK; **O7** lets an FDE stamp out a new app-brain per customer on top of both.

---

## 4. Research-derived capabilities to fold in (from the two sources)

Concrete items mined from the Claude Code Ultimate Guide and the Cursor changelog (July 2026), each mapped to where it lands in Cortex. These are **parking-lot / roadmap candidates**, not immediate work.

### 4a. From the Claude Code Ultimate Guide (github.com/FlorianBruniaux/claude-code-ultimate-guide)

| Pattern | Cortex landing spot | Note |
|---|---|---|
| **Safety hooks** — `dangerous-actions-blocker`, `prompt-injection-detector`, `unicode-injection-scanner`, `output-secrets-scanner` | Harden the agent write path (O3/O4) and the AirGPT trust boundary | Cortex already has `scripts/secrets_scan.py` + `injection_guard` + `pii.redact_for_prompt`; the guide's hook *taxonomy* (block dangerous → detect injection → scan output) is a good checklist to complete before tool #2 |
| **Permission tiers** (Restrict / Allow / Request) | Maps to the T0–T3 tier router + F7 RBAC | Formalize a "Request" (human-confirm) tier for `requires_confirm` action types (already in O3 for `export_pptx`) |
| **Context-pressure discipline** (50/70/90% thresholds, compact→clear) | Ponytail middleware + `cost_ledger` ceiling | Adopt explicit context-pressure thresholds in the orchestrator, not just a token cap |
| **Subagent personas** (code-reviewer, security-auditor, test-writer, output-evaluator) | `AGENTS.md` subagent roles + O6 agent builder | Ship evaluation as a first-class step (mirrors Palantir "AIP Evals") — see §4c |
| **Observability** (session dashboard, token-routing/RTK, multi-provider bridge) | `CortexOS/routing/cost_ledger.py` + tier router | An owner-facing "engine session dashboard" is a strong SDK differentiator |
| **Threat DB / MCP vetting workflow** (provenance → review → whitelist → sandbox → monitor) | The DMS_SOVEREIGN / egress + MCP trust story | Directly relevant when the engine SDK starts loading customer/third-party MCP servers |

### 4b. From the Cursor changelog (cursor.com/changelog, July 2026)

| Cursor capability | Cortex landing spot | Note |
|---|---|---|
| **Cursor Router** — intelligent model routing, modes *Intelligence / Balance / Cost*, across desktop/web/CLI/**SDK** | This is exactly `CortexOS/routing/model_router.py` + `tier_router.py` + Ponytail | Validation that per-request model routing with a cost mode is the right shape; consider exposing the 3 modes as an SDK knob |
| **Cloud agent hooks** — `beforeSubmitPrompt`, `afterAgentResponse`, `afterAgentThought`, `stop`, `subagentStart` | Add lifecycle hooks to the agent orchestrator (Brain B) | Gives the SDK observability + a control point to enforce governance mid-run (align with the ledger tamper bar) |
| **Parallel / side agents** | The two-orchestrator reconciliation watch-item (O-plan §6) | Don't build a third orchestrator; make parallelism a property of the one that survives |
| **Team MCP marketplace** (admin-configurable, group access controls) | Per-client MCP config + the "per-client agent config versioning" open problem | Salesforce "Local Assets" lesson (research idea #6): version-scope agent wiring per deployment |
| **Model routing enabled-by-default for Teams** | SDK default posture | Ship the engine SDK with a sane default routing/cost policy companies can override |

### 4c. Cross-cutting: evaluation is a first-class step

Both Palantir (AIP Evals) and the Claude Code guide (`output-evaluator`, verification-paradox warning: polished artifacts get −5.2pp less scrutiny) converge on the same lesson: **an agent builder without an evals harness ships unverifiable agents.** Add an `evals/` step to O6 (agent builder) before it is allowed to run for a paying client.

---

## 5. Parking-lot updates (added to `PARKING_LOT.md` this pass)

- **P14 — Engine-as-SDK / company dual-brain** (this doc). Condition: reconciliation option chosen (§2) + O1/O3/O4 shipped.
- **P15 — netie-engine ↔ main capability landing.** Condition: §2 decision made; then land one capability per gate.
- **P16 — Agentic hardening from research.** The §4 hook/permission/observability/evals items. Condition: before tool #2 / before the SDK loads third-party MCP.

These extend, not replace, the existing P1 (Palantir ontology parity) and P12 (company central brain) — P14 is the *how* under P1/P12.

---

## 6. Suggested roadmap (Fable 5 reorders freely)

```
W0  Reconciliation decision (§2)  ── ✅ DONE 2026-07-23: Option B via C
W1  Land O1 (ontology registry) + O2 (codebase map) on main      [no gate needed]
W2  Land O3 (action-type registry = F8) on main                  [needs F7 PASS — already green]
W3  Land O4 (Agent SDK surface) — "the SDK"                       [needs O1,O3]
W4  Capability landing #1 from netie-engine: L0 DuckLake lakehouse [own branch + CI]
W5  Capability landing #2: rawknn/hybrid-RAG memory store         [own branch + CI]
W6  O5 wire AirGPT runtime through the SDK (ledger-audited runs)
W7  Fold §4 hardening hooks + permission tiers + evals harness
W8  O7 new-pack generator (FDE payoff) + one demo pack (e.g. packs/crm/)
```

Parking-lot-gated (do **not** schedule until condition met): O6 agent builder (explicit owner go-ahead), O8 ingest→ontology (P6 condition), full P1/AIP UI, Firecracker (P2), PQ crypto (P11).

---

## 7. How to start (for the Fable 5 session)

1. Read this doc + `docs/ontology/CORTEX_ONTOLOGY_PLAN.md` + `STATUS.md` + `docs/strategy/FABLE5_HANDOFF_PROMPTS.md`.
2. §2 is **decided: Option B via C.** No further owner sign-off needed to start W1.
3. Start at **W1** (O1/O2 — pure internal plumbing, no gate, transcribes existing `semantic_layer.yaml`). Ship one gate, prove CI green, then the next — never a big-bang. Use the matching prompt from the handoff-prompts doc.
4. Keep `netie-engine` as the R&D feeder; land its capabilities into `main` one branch + one green CI at a time (§2 option C→B).
5. Update `STATUS.md` and move items out of `PARKING_LOT.md` only by explicit decision, per the repo's own rule.

---

## 8. What this plan deliberately does not do

- Does not merge/rebase the two brains now (§2 — that's an owner decision, and big-bang is rejected).
- Does not duplicate the O-series design — it references `CORTEX_ONTOLOGY_PLAN.md`.
- Does not claim Palantir/AIP parity — that framing stays gated on P1 per the repo's parking-lot rule.
- Does not touch F1/F5/F7 internals — they remain the enforcement spine the SDK routes through.
