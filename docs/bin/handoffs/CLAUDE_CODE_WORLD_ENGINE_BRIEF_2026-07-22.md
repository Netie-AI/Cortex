# Claude Code brief — World-driven agentic operations engine

**Audience:** Claude Code, acting as senior engineer / security architect / systems designer.  
**From:** Cursor orchestrator + research subagents (engine spine, FDE/multi-vertical, security track) · 2026-07-22.  
**Tone:** Outcomes, constraints, open problems. You own the design. Cursor can take mechanical slices after you hand back.

**Do not treat this as a ticket dump.** Invent better shapes if you find them. Argue with us when the docs are wrong — several are.

---

## 0. North star (one paragraph)

Build the **governed world-model that agents and humans reason over**, not another chatbot bolted to SQL.

- **World** = objects + links + actions (ontology-as-memory).  
- **LLM** = reasoner over that world, never source of truth.  
- **Software ops** = same write path for UI click, FDE tool, and agent tool-call: validate → compliance → execute → ledger.  
- **Verticals** = packs (warehouse today; CRM/SaaS/software ops tomorrow) on one CortexOS spine.  
- **Deploy** = forward-deployed engineer can stand up a client profile without rewriting core.

We are aiming at **Palantir AIP–class governance** and **Salesforce-class stickiness**, without rip-and-replace. Layer on incumbents; greenfield for Excel/paper. See `docs/dms/POSITIONING.md` and `docs/ontology/PALANTIR_AIP_RESEARCH.md`.

---

## 1. What research already settled (do not re-litigate)

Read in this order if cold:

| # | Doc | Why |
|---|---|---|
| 1 | `STATUS.md` | Live gate truth (prefer over stale ARCHITECTURE cells) |
| 2 | `docs/dms/TRUTH_GROUND_MAP.md` | Feature → file → test → state |
| 3 | `docs/ontology/CORTEX_ONTOLOGY_PLAN.md` | O0–O8 phases; fragments inventory |
| 4 | `docs/ontology/PALANTIR_AIP_RESEARCH.md` | Portable AIP patterns |
| 5 | `docs/dms/BUILD_PLAN_V2_LAKEHOUSE.md` | L/Q/S/U tracks already largely shipped |
| 6 | `docs/research/findings/P0_SECURITY_GAPS.md` + handback `CLAUDE_CODE_SECURITY_HANDBACK_2026-07-22.md` | Security judgment leftovers |
| 7 | `PARKING_LOT.md` | Hard gates — do not smuggle P1/P2/P11 mid-sprint without owner move-out |
| 8 | `docs/PLUG_AND_PLAY.md` | Current pack attach surface |

**Portable AIP ideas already endorsed:**

1. Ontology-as-memory; LLM-as-reasoner.  
2. One schema → UI + agent tools + permissions (stop three drifting registries).  
3. **Actions = only legal write path** (human and agent identical).  
4. Metadata as data (YAML → compile into ops DB), not a new DB per concept.  
5. Route agents through primitives ops already trust (brain/suggest/compliance/ledger).  
6. Governance is cross-cutting, not a bolted layer.

---

## 2. Honest inventory (research 2026-07-22 — verify before coding)

### Strong spine (shipped, load-bearing)

- DAG + cost ceiling: `CortexOS/execution/dag_runner.py`, `CortexOS/routing/cost_ledger.py`, `model_router.py`
- Fabrication: `dag_compiler.py`, `dsl_parser.py` (TOOL_CALL kind present), `skill_registry.py`, `skillmesh.py`
- **F8 tool-call path exists in code** (`tool_runner.py`, `action_routes.py`, `export_pptx`) — several docs still say “packet only”. **Fix docs or you’ll re-implement.**
- Q2 answer engine (abstain-safe): `CortexOS/dms/answer_engine.py`
- S1 agents: detect → draft → human approve → publish (`packs/dms/agents/*`)
- Lakehouse L0–L2, streams S0, semantic Q1 — BUILD_PLAN_V2 mostly landed
- Security choke-points: harness, reversible (flag), filetype intake, SOPS scan, RLS test design
- Pack attach: `packs.dms.plug_in`, sidecar secure/classify/audit

### Hollow or dangerous

| Issue | Why it matters |
|---|---|
| **Unauthenticated legacy write surface** | Research found `dms_query`, `warehouse_routes`, `chat_routes`, `/search`, `/run` missing `require_role` while newer routes are gated. “F7 PASS” is incomplete if demo UI still drives open writes. **Verify and close.** |
| Four ontology fragments, no registry | `semantic_layer.yaml` · SkillCard · compliance YAML · ledger string event types |
| Skill registry ≠ tool allowlist | `SkillCard.required_tools` not driving `tool_runner` allowlist |
| Dual tier routers | Ponytail regex vs `judgment_model` feature scorer — undocumented which wins |
| Empty stubs | `execution/tier_router.py`, `fabrication/{dead_code,causal_scanner,intent_router}.py`, `crypto/transport.py` |
| F8 “sandbox” honesty | `export_pptx` is host shim, not WASM; allowlist size = 1 |
| Memory content gate | RBAC on who; little/no PII gate on what lands in vectors |
| Pack abstraction unproven | `packs/ruma` exists but agents-only; `app.py` still `if pack.name == "dms"` |
| AirGPT source not in this repo | Only sidecar contract; don’t invent a second orchestrator here |
| Docs lag code | ARCHITECTURE F8 cell, GATE_F8_PACKET, CONTEXT gate lines, TRUTH_GROUND F8 row |

### Parking lot (owner must move out)

- **P1** full Palantir AIP UI/builder (O6+ looks like this — need explicit go)  
- **P2** production WASM/Firecracker  
- **P11** post-quantum  
- **P6** dirty Excel ingest automation (FDE gold) — condition: pilot dirty imports  
- **P10** FDE playbook — write after first paid pilot  

**O1–O3** in the ontology plan are framed as *internal plumbing* / F8 generalization and may proceed without claiming “AIP parity.” O6+ needs owner decision.

---

## 3. Mission for you (senior ownership)

You are the person who makes this a **world-driven engine**, not a feature factory.

### Outcomes we need from you

**A. Security architecture of the agent world**

1. Attest: is RLS CI actually green on the integration line? Define merge policy if red/skipped.  
2. Design+ship minimal real egress: `transport.egress_allowed` + `DMS_SOVEREIGN` default posture (you choose fail-closed vs fail-open — own the liability).  
3. Decide what “sandboxed tool” means before tool #2: host-shim OK for low-risk? WASM gate when?  
4. Extend reversible/PII contract to **memory writes + tool params** — who/what/false-negative budget.  
5. AirGPT trust boundary: same API keys forever, or signed provenance + reversible unmask endpoint? Blast radius.  
6. Ledger tamper bar when agents act: in-process hash chain enough for current tier, or external anchor needed before F8 scales?

**B. Unify the world model (pre–full P1)**

Without claiming Foundry parity, make agents able to answer: *what objects exist, what actions am I allowed, what fired on the ledger?*

Suggested starting shape (challenge freely):

- O1 registry YAML + compile into **same** ops DB as F1 (or argue Postgres-first if you reject SQLite metadata).  
- O3: tool allowlist + ledger event types resolve from `action_types` — **do not build a parallel F8.**  
- O2 codebase map (AST → `codebase_ontology.db`) is optional leverage for FDE/agent discovery — only if ROI is clear.

**C. Single blessed Agent SDK**

One in-process surface agents/runtimes import:

- `list_object_types` / `list_action_types`  
- `query_objects(..., actor)` → RBAC + PII  
- `call_action(..., actor)` → compliance + ledger  

No new HTTP circus required on day one. Kill the path where AirGPT (or future packs) get a flat `TOOLS` dict that bypasses F5/F7.

**D. Multi-vertical / FDE readiness (creative open)**

- Formalize pack profile so `PACK=x` is not an if-ladder forever.  
- Pressure-test with **RUMA wiring** or a tiny `packs/crm` scaffold — your call which proves more.  
- Secrets/config story for client-site FDE (beyond USB `env.local`) — design, maybe thin implementation.  
- Optional: `new_pack.py` generator once registry exists (O7).

**E. Truth hygiene**

Reconcile STATUS / ARCHITECTURE / TRUTH_GROUND / GATE_F8 / CONTEXT with code after your first pass. Lying docs create duplicate engines.

---

## 4. Constraints (hard)

- **NEVER TOUCH** audited security choke-points — compose beside (`pii`, `injection_guard`, `scam_guard`, `prompt_harness`, `photo_sanitize`, adversarial suite). `filetype_guard` is now wired → extend via `intake_policy`.  
- Do not weaken tests to green.  
- Do not commit AirGPT runtime data, secrets, `env.local`, `key.md`.  
- Do not invent Temporal / Firecracker / PQ crypto without parking-lot move-out.  
- Do not build a third orchestrator. AirGPT’s queue (if it exists out-of-repo) stays a boundary.  
- Layer, don’t rip-and-replace Salesforce/WMS/Snowflake.  
- Prefer stdlib / YAGNI (Ponytail) before new frameworks.  
- Hand back Cursor-sized slices when work is mechanical; keep judgment work yourself.

---

## 5. Open problems — we want your judgment, not our steps

These are the questions we **deliberately leave open** for you:

1. **Auth remediation:** mechanical `require_role` on legacy V0 routes, or migrate writes onto F8/action-types as the only path? Cost vs honesty.  
2. **Ontology storage:** SQLite-in-ops-DB (ontology plan) vs Postgres-first metadata — avoid a second migration later.  
3. **Tier authority:** merge Ponytail + JudgmentModel, or document dual paths forever?  
4. **When does O6 (LLM agent builder) leave the garage?** Paying client? F7 true PASS? Your threat model?  
5. **Second vertical:** finish RUMA as proof, or scaffold CRM from Salesforce UDD patterns in research §5?  
6. **RAG ↔ DMS ingest:** finish `CortexOS/rag` for “digest customer files,” or wait for P6? Who owns the vestigial ListingDocument cleanup?  
7. **Version-scope agent wiring** (Salesforce Local Assets lesson): how does one client’s agent config not poison another sharing skill libraries?  
8. **Software-company vertical:** is “layer on Jira/CI” the same Wedge A pattern as WMS, or a different integration primitive?

---

## 6. Suggested workstreams (reorder / merge / invent)

Not a schedule — a landscape. Pick a critical path and tell us why.

```
W0  Truth pass — docs = code (F8, F7 auth surface, STATUS)
W1  Security close — legacy RBAC + RLS attest + egress posture
W2  World registry — O1 (+ optional O2) without AIP marketing claims
W3  Action unity — allowlist + ledger events from action_types; expand tools under your sandbox bar
W4  Agent SDK — one import surface; kill bypass paths
W5  Pack profile — declarative pack; prove with RUMA or crm scaffold
W6  FDE friction — secrets, doctor/preflight, “point at customer files” design (may touch P6 later)
W7  Studio / observability — U0 when world+agents are queryable (Cursor can own UI)
```

Cursor is good at: route deps, Studio tabs, stress harness code, `@agent` chat parse, metric YAML.  
You are good at: threat models, registry design, SDK boundaries, crypto/egress, red-team, deciding when “enough” for pilot.

---

## 7. Acceptance vibe (how we’ll know you’re winning)

Not a checklist theater — signals:

- An agent cannot mutate warehouse/CRM state except through a registered action that leaves a ledger event.  
- A new vertical can be described mostly as YAML + SQL seed, not a fork of CortexOS.  
- Demo UI cannot write without a role; CI proves RLS when DSN present.  
- Outbound model/cloud calls obey an explicit sovereign/egress policy.  
- Docs and STATUS match the tree after each wave.  
- You can explain the engine to an FDE in one whiteboard: world → reason → act → audit.

---

## 8. How to collaborate with Cursor after this

Hand back in the usual packet style:

1. **Decisions** you locked (with one-line rationale).  
2. **What you shipped** (paths + tests).  
3. **Cursor slices** (file-sized, acceptance bars).  
4. **What stays parking-lot** (explicit).  
5. **What you need from the owner** (O0 go/no-go for O6+, P1 move-out, pilot timing).

Update `STATUS.md` + `CHANGELOG_DMS.md` when gates flip. Prefer appending a handback sibling to this file rather than rewriting history.

---

## 9. Closing note to Claude Code

You have the rare job of making this feel inevitable: a small Malaysian ops stack that thinks like Foundry and sticks like Salesforce, without pretending either product exists yet.

Be creative. Be severe on security. Be lazy where YAGNI wins. Be honest when the world model is still four YAML files that don’t know each other.

We’re here to assist — not to over-instruct. Treat this brief as a briefing from a peer who did the reading so you can spend your mind on the hard cuts.

— Cursor orchestrator · Netie Cortex
