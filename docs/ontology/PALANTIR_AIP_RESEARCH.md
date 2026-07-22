# Palantir AIP / Foundry Ontology — Research Notes

**Purpose:** external research feeding `CORTEX_ONTOLOGY_PLAN.md`. This file is research only — no repo code is described here beyond what's needed for contrast. Do not treat anything in this file as shipped in Cortex.

**Date:** 2026-07-18. Sources are Palantir's own docs site (`palantir.com/docs/foundry/...`), Palantir's engineering blog, and third-party analysis pieces — flagged where third-party.

---

## 1. Palantir Foundry Ontology — core concepts

Source: [Ontology overview](https://www.palantir.com/docs/foundry/ontology/overview), [Core concepts](https://www.palantir.com/docs/foundry/ontology/core-concepts), [Object and link types — type reference](https://www.palantir.com/docs/foundry/object-link-types/type-reference), [Object types overview](https://www.palantir.com/docs/foundry/object-link-types/object-types-overview), [Link types overview](https://www.palantir.com/docs/foundry/object-link-types/link-types-overview), [Properties overview](https://www.palantir.com/docs/foundry/object-link-types/properties-overview)

The Ontology is described as "a categorization of the world" — an organization's digital twin built from four primitives, plus two cross-cutting layers:

| Primitive | What it is | Rough DB analogy |
|---|---|---|
| **Object type** | Schema for a real-world entity or event (e.g. `Airport`). Instances = objects; collections = object sets. | Table |
| **Property** | A characteristic of an object type (a column). Properties can be *shared* across object types for consistent modeling. | Column |
| **Link type** | Schema for a relationship between two object types. An instance is a *link*. | Join / FK relationship |
| **Action type** | Schema for a bundle of edits (object/property/link changes) a user or agent can submit *together*, plus the side effects that fire on submit. | Stored procedure / transaction |

Two things sit on top of the four primitives:

- **Functions** — code-based logic (TypeScript/Python) that is natively integrated with the Ontology: they take objects/object-sets as typed inputs, read property values, and can be called from actions, automations, or agents. Functions are how business logic becomes a callable, ontology-aware unit rather than a one-off script.
- **Interfaces** — a polymorphism layer: an interface describes a *shape* (a set of properties/behaviors) that multiple unrelated object types can implement, so code and UI can operate generically ("any object with a `LocationInterface`") instead of hard-coding one type at a time.

**Governance is baked into the same primitives, not bolted on:**
- **Roles** are the central permissions model — access can be granted at the whole-Ontology level or on individual object types / properties / actions.
- **Object views** are auto-generated information hubs per object (details, linked objects, metrics, related workflows) — so governance and UI both derive from the same schema instead of being hand-built per app.

**Why this matters for an agent platform:** because object types, link types, and action types are the *same* artifacts that (a) generate the UI, (b) define what an LLM agent is allowed to read/write, and (c) are the unit of permission-checking — there is one source of truth instead of three (a DB schema, an API spec, and a permissions table that drift apart over time).

---

## 2. Action types — the governed writeback mechanism

Source: [Action types overview](https://www.palantir.com/docs/foundry/action-types/overview), [Action types — rules](https://www.palantir.com/docs/foundry/action-types/rules)

An **action type** is "the schema definition of a set of changes or edits to objects, property values, and links that a user can make all at once," plus its side effects. Key properties:

- **Parameters**: typed inputs to the action, with configurable defaults, dropdown filtering, and override rules — this is what turns a raw API call into a governed, UI-renderable form.
- **Validation / rules**: e.g. auto-create-link rules, and permission checks so only an authorized role (their example: HR staff) can submit a given action.
- **Side effects on submit**: notifications, webhooks to external systems, scheduled builds — these are declared on the action type, not hand-wired per call site.
- **Writeback**: once submitted, changes commit to the Ontology's writeback dataset and are immediately visible to every application built on that Ontology (no separate "sync back to source" step).
- **Invocation**: the *same* action type, with the *same* validation, is invoked identically whether the caller is a human clicking a button in a Foundry app or an AIP agent calling it as a tool — there is one governed choke point for "how does state change," not one path for humans and a looser one for agents.

**Pattern to borrow:** actions are the *only* legal write path. Nothing — human UI or LLM agent — mutates the Ontology by any other route, which is what makes "agent had write access" an auditable, revocable, and rate-limitable fact rather than a hope.

---

## 3. AIP — how agents sit on top of the Ontology

Sources: [AIP overview](https://www.palantir.com/docs/foundry/aip/overview), [AIP architecture overview](https://www.palantir.com/docs/foundry/architecture-center/aip-architecture), [AIP features](https://www.palantir.com/docs/foundry/aip/aip-features), [AI ethics and governance](https://www.palantir.com/docs/foundry/aip/ethics-governance)

AIP's architecture is a data-to-action pipeline layered on the Ontology, not a chatbot bolted next to the database:

1. **Data foundation** — batch/streaming/real-time ingestion (Spark/Flink-class runtimes) integrates raw sources.
2. **Ontology layer** — activates that raw context into "a unified representation of enterprise decision-making," modeling operational processes into a form "legible for both humans and agents." This is the load-bearing claim: the Ontology is explicitly built to be the interface agents reason over, not a side artifact.
3. **Agent & automation layer** — agents are built with a spectrum of tooling: no-code visual builders up to pro-code workspaces (see AIP Logic / Agent Studio below), orchestrated via low-code logic chains or full code workspaces.
4. **Action layer** — operational execution: schedule-based, event-driven, or API-driven, and it "leverages Ontology primitives" — i.e. an automation doesn't call a bespoke integration, it calls an action type.

**Security/governance is explicitly cross-cutting**, not a layer of its own: "every operation made by humans and agents abides by rigorous role-, marking-, and purpose-based controls," enforced identically whether the caller went through the UI, an API/SDK, or an agent. The stated goal is that governance, change management, and audit logging apply *identically* to human and agentic actions — agents don't get a side door.

### Agent construction: AIP Logic / AIP Agent Studio

Sources: [AIP Agent Studio overview](https://palantir.com/docs/foundry/agent-studio/overview/), [AIP Agent Studio — Foundry APIs](https://www.palantir.com/docs/foundry/agent-studio/foundry-apis), [Workshop — AIP Agent widget](https://www.palantir.com/docs/foundry/workshop/widgets-aip-agent)

- AIP Agent Studio (formerly "AIP Chatbot Studio") is the builder for interactive assistants ("AIP Chatbots"/"AIP Agents") equipped with enterprise-specific information and tools, deployable inside Foundry apps or externally through the Ontology SDK / platform APIs.
- Agents are described as powered by "LLMs, the Ontology, documents, and custom tools" — three distinct context sources an agent can be wired to: (a) **Ontology context** — query real business objects/links directly, (b) **document context** — search unstructured knowledge bases, (c) **custom function-backed context** — call a Function for bespoke logic.
- AIP Logic and AIP Evals are named as the companion builder tools: AIP Logic for low-code chains of Ontology-aware logic, AIP Evals for testing agent behavior before it ships — evaluation is a named first-class step in the toolchain, not an afterthought.
- Construction explicitly spans "the full spectrum from no-code visual builders to pro-code workspaces" — the same agent concept is buildable by a business user or a software engineer depending on complexity, which is directly relevant to a forward-deployed-engineer (FDE) workflow where speed matters more than code purity for a first pass.

### Governance detail (third-party synthesis, cross-checked against Palantir's own governance page)

Source: [Palantir AIP Agent-Ontology Interaction — five-layer architecture](https://zerofuturetech.substack.com/p/palantir-aip-agent-ontology-interaction) *(third-party blog, not Palantir-authored — treat as interpretation, not spec)*

This piece frames the same material as five layers: **Retrieval Context → Object Query → AIP Logic → Action Tools → Governance**, adding a useful framing that "Action Tools encapsulate write operations as governed actions with parameter validation, permission checks, and user confirmation," and that "tool invocations are dependent on access to the underlying objects, properties, and links in the Ontology" — i.e. an agent cannot be granted a tool that reaches further than the Ontology permissions already allow; the tool is a thin wrapper, the Ontology is the actual gate.

A second third-party synthesis — [Palantir's 12-layer agentic architecture](https://anandbg.com/blog/palantir-aip-end-to-end-agentic-architecture) *(third-party, unofficial numbering — treat layer count/names as one analyst's read, not Palantir's own taxonomy)* — is useful mainly for one framing worth keeping: **"the Ontology serves as persistent memory... rather than relying on the LLM as the agent's brain."** That inversion (Ontology-as-memory, LLM-as-reasoner-over-that-memory, not LLM-as-source-of-truth) is the single most portable idea from all of this research and is the organizing principle for Part 2 of this research and for the plan document.

The same synthesis names three operational planes worth borrowing as vocabulary even though Cortex won't build 12 discrete layers: a **semantic data layer** (unifies structured/unstructured/streaming/geospatial data), a **dynamic logic layer** (connects AI to business logic and models — this is "Functions"), and a **kinetic action layer** (routes agent decisions back into operational systems — this is "Action types" reaching outward).

### Palantir's own framing: agents connect to decisions, not just data

Source: [Connecting Agents to Decisions — Palantir Blog](https://blog.palantir.com/connecting-agents-to-decisions-277dee8ddb40) *(Palantir-authored; page redirects through Medium's identity wall so only the title/URL could be confirmed, content not independently re-verified beyond the title — cite with that caveat)*

Title and framing confirm the throughline of the rest of the docs: the pitch is not "agents that answer questions," it's "agents that are safely wired into the same decision/action substrate humans use," which only works because actions are typed, permissioned, and validated at the Ontology level before an agent (or human) ever reaches them.

---

## 4. ServiceNow — metadata-driven platform (non-AI-agent comparison)

Sources: [ServiceNow architecture explained](https://www.servicenow.com/community/architect-articles/servicenow-architecture-explained-simply-platform-workflows-and/ta-p/3471257), [ServiceNow metadata in applications](https://www.servicenow.com/docs/r/application-development/servicenow-metadata-in-applications.html), [Metadata, tables, and records](https://www.servicenow.com/docs/r/application-development/developer-sandboxes/dev-sbx-metadata.html), [Table API reference](https://www.servicenow.com/docs/r/api-reference/rest-apis/c_TableAPI.html)

- ServiceNow is architecturally one shared database: opening "Incident" doesn't mean entering a separate system — it's a view over shared tables, so **one record can drive multiple business processes** (an incident referenced by IT ops, then linked into a risk/compliance workflow) without duplication.
- **Metadata-driven configuration**: developers define the data model with graphical tools and access it via dot notation (`table1.table2.field`) instead of hand-writing join/mapping code — the platform generates the plumbing from the schema.
- All table metadata lives in one system table, `sys_db_object` — every custom table, and every built-in one, is itself a row describing a table. This is the same "metadata about metadata" pattern as Salesforce's UDD below, and the same pattern this plan proposes for Cortex's object-type registry (a table of tables).
- The Table API and schema-introspection ACLs expose *structural* metadata (table/field names, types) separately from *record* data — a useful access-control split: "can you see the shape of the CRM" is a different permission than "can you see this customer's row."

**2026 governance angle** (sources: [ServiceNow deepens AI agent governance — Forbes](https://www.forbes.com/sites/victordey/2026/05/05/servicenow-deepens-agent-governance-as-enterprise-demand-counters-saaspocalypse/), [ServiceNow AI Control Tower — Knowledge 2026 press](https://newsroom.servicenow.com/press-releases/details/2026/ServiceNow-turns-enterprise-AI-chaos-into-control-with-the-platform-for-governed-autonomous-work/default.aspx)): ServiceNow's 2026 pitch is explicitly "AI Control Tower" — an **AI Agent Orchestrator** that coordinates teams of agents, plus a control layer that can detect an agent exceeding its permissions and shut it down in real time. This is the same "agents are governed the same way humans are, via the same metadata" idea as Palantir, arriving at it from the ITSM/workflow side instead of the data-ontology side. Relevant to Cortex mainly as validation that "governance layer that also gates AI agents" is where the market converged in 2026, not a Palantir-only idea.

---

## 5. Salesforce — metadata-driven platform + Agentforce

Sources: [Salesforce architecture — Trailhead](https://trailhead.salesforce.com/content/learn/modules/starting_force_com/starting_understanding_arch), [Force.com multitenant architecture whitepaper](https://www.developerforce.com/media/ForcedotcomBookLibrary/Force.com_Multitenancy_WP_101508.pdf), [What is Salesforce metadata — Salesforce Ben](https://www.salesforceben.com/what-is-salesforce-metadata/), [Metadata: Software The Way You Want It — Salesforce Engineering](https://engineering.salesforce.com/metadata-software-the-way-you-want-it-2367b179558d/)

- Classic Force.com architecture: objects/fields/relationships/config are **all metadata**, stored in a small number of system tables that form the "Universal Data Dictionary" (UDD) — `MT_Objects` (object id, org id, object name) and `MT_Fields` (field id, org id, owning object id, field name, data type, indexed flag, field position). Every tenant's custom schema is *rows in these tables*, not actual `CREATE TABLE` DDL — this is what makes the platform reconfigurable per-org without code changes.
- This is the direct ancestor of "ontology as a registry, not as code": Salesforce proved at scale (decades of multi-tenant SaaS) that representing "what fields exist, what type, what object owns them" as *data* rather than *schema* lets a single engine serve arbitrarily different customer data models.

### Agentforce (2026) — how Salesforce wires agents to that metadata

Sources: [The new Agentforce metadata and development lifecycle — Salesforce Developers Blog](https://developer.salesforce.com/blogs/2026/05/new-agentforce-metadata-and-development-lifecycle), [Agentforce metadata types — Developer docs](https://developer.salesforce.com/docs/einstein/genai/references/agents-metadata-tooling/agents-metadata.html), [Architecting Agentforce custom actions — SFDC Developers](https://sfdcdevelopers.com/2026/02/20/architecting-agentforce-custom-actions-apex-metadata/)

- **`GenAiPlannerBundle`** — one per agent, the container for all topics + actions that agent can use when talking to an LLM.
- **`GenAiPlugin`** (= a **Topic**) — a category of related actions for "a job to be done"; an agent has many topics.
- **Actions** are backed by existing metadata primitives — **Apex, Flow, or a Prompt Template** — i.e. Agentforce doesn't invent a new execution model, it points agent actions at the same automation building blocks admins already use for humans. This mirrors the Palantir "same action type for human and agent" principle from a different implementation angle.
- **Atlas Reasoning Engine** (their planner) reads the agent's Topic Classification Description to pick a topic, then reads that topic's Scope/Instructions/Action Description to decide which action to invoke — routing is driven by metadata fields on the topic/action records, not by hard-coded if/else.
- 2026 shipped **"bundleization"**: all metadata for one agent version now deploys as one folder, and **Local Assets** let you clone a global action/topic and anchor the edit to a single agent version so iterating one agent can't silently break another agent sharing the same action. Directly relevant lesson for a multi-tenant/multi-customer builder: **version-scope the wiring, not just the definition**, or two customer builds sharing one library will step on each other.

---

## 6. sales-skills/sales — GitHub check

`https://github.com/sales-skills/sales` **exists**. It is an AI-powered sales copilot: "hundreds of skills for prospecting, outbound, deals, proposals, and GTM," built as Claude Agent SDK skills, installed via `npx skills add sales-skills/sales`. Structure: one central router skill (`/sales-do`) that parses a natural-language request and routes to the right specialized skill; ~40+ cross-platform "strategy" skills (e.g. email deliverability, help-desk selection) that get tried before platform-specific skills; and 100+ platform-specific skills (Apollo, Mailshake, HubSpot, Salesforce, etc.) organized by domain (prospecting/pipeline, active deals/forecasting, outreach platforms, CRM/marketing automation, conversation intelligence, data enrichment, transactional infra).

**Relevance to Cortex:** it is not an ontology — it's a flat, well-organized *skill library with a router*, which is closer to what `packs/dms/skills/capture.py` (F6) and `CortexOS/skills/library.py` are already building than to Palantir's object/link/action model. The one transferable pattern worth stealing: **a single router skill that asks clarifying questions before dispatching**, rather than making the caller pick the exact skill name. That maps cleanly onto a future `sales`/CRM vertical pack for Cortex (a `packs/crm/` analogous to `packs/dms/`) — see the plan doc for where that would land. It does **not** offer a CRM ontology (object types for Account/Contact/Opportunity/etc.) — for that, the Salesforce metadata research above (§5) is the closer reference, since a "build a CRM like Salesforce" customer ask is really asking for Salesforce's own object model (Account, Contact, Lead, Opportunity, Case) re-hosted, not for the sales-skills prompt library.

---

## 7. Summary — the portable ideas

1. **One schema, three consumers.** Object/link/action types simultaneously define (a) what the UI renders, (b) what an agent may read/write, (c) what a permission check evaluates. Cortex today has three separate half-answers to this (`semantic_layer.yaml` for NL→SQL, `SkillCard` for tool capability, `dms_rules_v1.yaml` for compliance) that don't share a registry.
2. **Actions are the only write path**, identically enforced for humans and agents, with typed parameters, validation rules, and declared side effects — never an ad hoc mutation.
3. **The Ontology is the agent's memory**, not the LLM. The LLM reasons *over* a governed object graph; it doesn't hold enterprise facts in its weights or in an ungoverned prompt-stuffed context window.
4. **Metadata is data, not code** (Salesforce's UDD, ServiceNow's `sys_db_object`) — a customer-specific data model should be *rows describing objects/fields*, not a bespoke Python class per customer. This is the mechanism that lets one engine serve N different customer verticals.
5. **Actions route through the same automation primitives admins already trust** (Salesforce: Apex/Flow/Prompt Template; Palantir: Functions) — don't invent a second execution model just because the caller is now an LLM.
6. **Version-scope agent wiring per deployment** (Salesforce's 2026 Local Assets lesson) so that building/iterating one customer's agent can't break another customer's agent sharing the same underlying skill/action library.
7. **Governance is cross-cutting infrastructure, not a layer** — evaluated at every read/write regardless of caller, with the same audit trail for human and agent action. This is the one principle Cortex already substantially has (F1 hash-chained ledger, F5 compliance gate, F7 RBAC) — the gap is that it isn't yet keyed to a shared object/action registry, it's keyed to hand-written routes.
