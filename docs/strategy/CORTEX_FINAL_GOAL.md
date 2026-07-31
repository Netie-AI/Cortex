# Cortex — Final Goal: The Best Engine

**Date:** 2026-07-23 · **Status:** north-star (owner-set). Everything Cortex builds serves this.
**Companion:** `docs/strategy/ENGINE_SDK_DUAL_BRAIN_PLAN_2026-07-23.md` (how) · `docs/bin/handoffs/FABLE5_HANDOFF_PROMPTS.md` (historical O-series prompts; O1–O7 largely shipped).

---

## The goal, one line

> Cortex is **the best engine** for governed, agentic, LLM-powered systems — and nothing else.
> We improve only the **orchestration** and the **engine capability itself**. Every vertical, app,
> or UI is a *consumer* of the engine, not part of it.

## Scope — what Cortex is / is not

**Is:** the orchestration layer (DAG + tier/model routing, cost governance, agent lifecycle, durable
resume) **plus** the engine capabilities (hybrid retrieval, memory, semantic/answer, streams, lakehouse,
ontology + governed actions, context engineering). The reasoning/runtime brain.

**Is not:** a vertical product. The DMS warehouse app, RUMA, a CRM are **reference consumers** that
prove the engine — not the thing we sell. When a capability could live "in the engine" or "in an app,"
it goes in the engine.

## How the outside world uses it — two modes

The **API layer** is the product surface for external builders (see PARKING_LOT **P17**):

1. **Hosted API** — call Cortex over the wire and use **only the parts you need**: orchestration,
   retrieval, memory, model routing, ontology/action governance, context assembly. Pick-and-compose;
   you don't have to take the whole thing.
2. **Download & self-host ("netie engine")** — pull the engine, **configure it** (models, storage,
   routing, governance policy), and run it **against your own data/base**, on your own infra — for
   sovereignty, air-gap, or deep integration.

Both modes hit the **same governed core**: actions are the only write path; every read/write is
RBAC + ledger enforced, **identically for human and agent** (the ontology spine, O1–O5).

## What "best engine" requires (the bars)

- **Orchestration:** per-request model routing with a cost mode (Intelligence / Balance / Cost),
  durable resume, agent lifecycle hooks, one blessed `call_action` / `query_objects` SDK — no bypass paths.
- **Engine capability:** hybrid retrieval + memory, lakehouse time-travel, streaming, ontology-as-memory,
  layered context engineering, sandboxed tool execution.
- **Trust:** governance is cross-cutting (F1 ledger, F5 compliance, F7 RBAC) — the same hosted or self-hosted.
- **Legibility:** an outside engineer can adopt and configure it **without reading the source** — which is
  why the deliverables below are first-class, not afterthoughts.

## Deliverables that make the engine adoptable (first-class — PARKING_LOT P18)

1. **Strong API documentation** — every engine surface (orchestration, retrieval, memory, ontology/actions,
   context) with contracts, examples, and auth. The API *is* the product; its docs are part of the product.
2. **Whitepaper** — the design thesis: ontology-as-memory + LLM-as-reasoner + actions-as-only-write-path;
   why the dual-brain split; how governance stays identical across hosted and self-host.
   **Canonical file:** [`CORTEX_WHITEPAPER.md`](CORTEX_WHITEPAPER.md) (P18 thesis + ecosystem + branch map).
3. **Full, thorough understanding (architecture reference)** — built on the O2 codebase map + the dual-brain
   plan, so adoption, configuration, and contribution never require tribal knowledge.

## How this refines the dual-brain plan

- **Brain B (`netie-engine`) is the product** — the engine we make the best. Everything lands here (or in
  the shared core it becomes).
- **Brain A (`main` / DMS) is the first reference consumer** — it proves the engine and inherits the
  governance spine, but it is not the thing we sell.
- The Ontology **Agent SDK (O4)** = the in-process engine API; the **hosted API layer (P17)** exposes it
  over the wire; the **download/self-host packaging (P17)** ships the engine to run anywhere;
  **API docs + whitepaper (P18)** make it adoptable.

> One-line test for any proposed work: *"Does this make the engine better, or is it just another app on top?"*
> If the latter, it belongs in a consumer pack, not in Cortex.

---

## Enterprise objective binding (consumer goals on the engine)

Cortex does **not** become a “make money” app. Consumer orgs **bind** an ethical enterprise-value
goal to the engine’s gen-cFSM + JEPA stack:

- **Key posture — proactive first:** the engine **actively seeks** the bound goal (predict needs,
  prep work, close loops) even when the user is silent. Reactive inbox/event handling is secondary.
  Litmus: *if nobody messages for an hour, does the engine still advance \(g\) safely?*
- **Objective:** increase enterprise value (revenue / margin / retention / cash) **ethically** —
  safe, secure, consentful, non-deceptive; F5/F7 vetoes always outrank collapse scores.
- **Everyday loop:** proactive seeker → JEPA-rank next actions → constrained DAGs → audit →
  compressible action traces; open-set ingress (email/chat/ops) interrupts when novel work arrives.
- **Local continuity:** signed daily update port + minimal OAuth (account/device) so self-host
  stays current without a fat shared control plane — see P17 + **P21**.

Full program: `docs/strategy/ENTERPRISE_GEN_CFSM_LOOP_PLAN.md` (phases G2.0–G2.6).
