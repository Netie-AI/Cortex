# Cortex Ontology Plan — mapping AIP/Foundry concepts onto this codebase

**Status: PLAN ONLY. Nothing in this document is built. Do not present anything below as shipped.**
Companion doc: `docs/ontology/PALANTIR_AIP_RESEARCH.md` (external research + citations this plan draws from).

Read first if you haven't: `ARCHITECTURE.md`, `CONTEXT.md`, `AGENTS.md`, `STATUS.md`, `PARKING_LOT.md`.

---

## 0. The gate tension — read this before scheduling anything

`PARKING_LOT.md` already has an entry for this exact idea:

> **P1 — Full Palantir ontology + AIP parity.** Governed semantic objects, lineage, actions. **Condition:** 1+ paying clients, F1–F7 production-hardened.

`CONTEXT.md` lists "Palantir ontology (P1)" under **Not in scope now**, and the repo's own process for moving something out of the parking lot is explicit: *"1. Condition met. 2. Claude gate or explicit decision. 3. Add to `docs/dms/BUILD_PLAN.md`. 4. Update `STATUS.md`. Never mid-sprint."*

The condition (paying client + F1–F7 hardened) is **not met today** (STATUS.md: F7 remainder still in progress, F8 not started). This plan does not pretend otherwise. What it does instead:

- **Splits the work into phases that are honest about which side of the gate they're on.** Early phases (O1–O3) are internal plumbing and a generalization of work already in scope (F8) — they don't make a new customer-facing "ontology" claim, so they don't need the P1 condition to start.
- **Flags the phase where that stops being true** (O6 onward starts to look like an actual AIP-Agent-Studio-style feature) and says so explicitly, so nobody ships past the gate by accident.
- Leaves the actual PARKING_LOT.md / STATUS.md edits to the owner, per the repo's own rule that gate moves are an explicit decision, not something a plan document does on its own.

If you (the owner) read this and decide to move P1 out of the parking lot now, that's a valid explicit decision — just make it in STATUS.md/PARKING_LOT.md, not silently by starting to code.

---

## 1. What already exists that is ontology-shaped (inventory, not aspiration)

Cortex already has four independent, *partial* answers to "what does the AIP Ontology do." They don't share a registry today. That's the actual gap — not "no ontology exists," but "four fragments exist and don't know about each other."

| Fragment | File | Plays the role of | Gap vs. a real ontology |
|---|---|---|---|
| NL→SQL semantic layer | `packs/dms/semantic_layer.yaml` | Object types (`tables:`) + link types (`joins:`) + a glossary | No action types, no permissions, no function registry, not queryable except by the SQL router |
| Tool capability schema | `CortexOS/fabrication/skill_registry.py` (`SkillCard` pydantic model) | A per-skill "action type" (`required_tools`, `required_network`, `max_tier`) | Not linked to object/link types; no object-level read/write scoping, just tool-level |
| Compliance rules | `packs/dms/compliance/dms_rules_v1.yaml` + `CortexOS/compliance/engine.py` | Action-type validation rules | Rules are keyed to task/message shape, not to a formal action-type parameter schema |
| Hash-chained ledger | `packs/dms/audit/ledger.py` | Action-type writeback / audit trail | Already pack-agnostic (`repo_root()`, `default_db_path()` don't hardcode "dms") — genuinely reusable, just not yet reused |
| Agent tool loop | `CortexOS/AirGPT/agent_tools.py` (`TOOLS` dict) | A flat, hand-registered tool set an agent can call | No compliance gate, no ontology scoping, no RBAC — audited via `audit.log` + optional `cortex_client.append_ledger`, but nothing stops a tool from being added without going through F5/F7 |
| Agent definition CRUD | `CortexOS/AirGPT/agent_store.py` (`create_agent`) | The seed of an "agent builder" — name, kind, skill_pack, tools, memory_scope | Tools are picked from the flat `TOOLS` dict, not from a governed object/action registry |
| Agent run queue/worker | `CortexOS/AirGPT/cortex_orchestrator.py` | A lightweight agent manager (queue, thinking-mode, worker thread) | Separate from and unaware of the DAG-level orchestrator below — two orchestrators today, not reconciled |
| DAG execution | `CortexOS/execution/dag_runner.py`, `tier_router.py`, `model_router.py`, `CortexOS/routing/cost_ledger.py` | The real "orchestrator + agent engine" (tiered routing T0–T3, cost ceiling, DAG nodes) | `TOOL_CALL` node kind is unimplemented (`UnsupportedDAGNodeKind`) — this is exactly F8's scope |
| Sidecar bridge | `CortexOS/api/sidecar_routes.py`, contract in memory file `airgpt-cortex-sidecar-contract.md` | Today's only ontology-adjacent thing AirGPT can reach: `secure_message`, `classify_message`, `append_ledger`, `verify_ledger` | Security/audit only — AirGPT cannot query or act on DMS objects through this bridge today |
| Unstructured memory | `CortexOS/AirGPT/memory_index.py` (`ingest_text`, `embed_upsert`/Chroma, `search`, `thin_memories`) | The closest existing "chunk/digest into memory" pipeline | Lives in AirGPT (untracked in the Cortex repo per the sidecar-contract memory note), not in Cortex/packs; Cortex's own `CortexOS/rag/*` is a stub (`indexer.py` still has a vestigial `ListingDocument`/`chunk_listing` from an unrelated real-estate prototype) |
| Repo bootstrap | root `scaffold.py` | A one-time original skeleton generator (`netie/fabrication`, `netie/execution`, etc.) | Stale — references pre-alias `netie/*` paths directly; **not** a per-customer app generator and should not be extended into one |

Everything below either (a) generalizes one of these fragments into a shared registry, or (b) fills a genuine gap. Nothing proposes replacing F1 (ledger), F5 (compliance engine), or F7 (RBAC) — those stay exactly as they are and become the enforcement mechanism *for* the ontology, per research §7 point 7 ("governance is cross-cutting, not a layer").

---

## 2. Proposed ontology layer

### 2.1 Source of truth: per-pack YAML, same precedent as `semantic_layer.yaml`

New directory `packs/dms/ontology/` (sibling to the existing `semantic_layer.yaml`, which is left untouched — F5/F8 and the NL→SQL router depend on its current shape, don't destabilize it):

```
packs/dms/ontology/
  object_types.yaml     # entities: Inventory, Supplier, Location, Shipment, Transaction, Alert
  link_types.yaml        # relationships: mirrors semantic_layer.yaml's joins:, adds cardinality + semantics
  action_types.yaml      # NEW — the governed write-path registry (see 2.3)
  functions.yaml         # NEW — callable business-logic registry (see 2.4)
  registry.py            # loader + SQLite compiler (see 2.2)
```

`object_types.yaml` for the existing DMS pack is a transcription, not new modeling — it should say nothing that `semantic_layer.yaml`'s `tables:` block doesn't already say, plus three additions Foundry's model has and `semantic_layer.yaml` doesn't: a primary key, a human description per object type (for agent grounding), and an `agent_visible: true/false` flag per property (so `sensitive_columns:` — `email`, `phone`, `contact_person` — becomes a property-level flag instead of a separate list that can drift out of sync).

Example shape (illustrative, not final):

```yaml
# packs/dms/ontology/object_types.yaml
object_types:
  - id: inventory
    description: "A SKU held at a location, with reorder and expiry state."
    primary_key: sku
    properties:
      - {name: sku, type: string, agent_visible: true}
      - {name: quantity_kg, type: number, agent_visible: true}
      - {name: reorder_level_kg, type: number, agent_visible: true}
      - {name: is_hazardous, type: boolean, agent_visible: true}
  - id: supplier
    description: "A vendor Inventory items are sourced from."
    primary_key: supplier_id
    properties:
      - {name: supplier_name, type: string, agent_visible: true}
      - {name: contact_person, type: string, agent_visible: false}   # was semantic_layer.yaml sensitive_columns
      - {name: email, type: string, agent_visible: false}
      - {name: risk_score, type: number, agent_visible: true}
```

### 2.2 Runtime form: compiled into the existing SQLite ops DB, not a new database

Per research §7 point 4 (metadata as data, one engine, not a bespoke store per concept): compile the YAML into tables inside the **same** `data/dms_ops.db` the F1 ledger and V0/V1 warehouse tables already live in — `ontology_object_types`, `ontology_properties`, `ontology_link_types`, `ontology_action_types`, `ontology_functions`. `packs/dms/ontology/registry.py` does the compile, in the same style as `packs/dms/audit/ledger.py` (plain functions, stdlib `sqlite3`, no framework coupling):

```python
def load_object_types(pack_dir: Path) -> list[ObjectType]: ...
def load_link_types(pack_dir: Path) -> list[LinkType]: ...
def load_action_types(pack_dir: Path) -> list[ActionType]: ...
def compile_to_sqlite(pack_dir: Path, db_path: Path) -> None: ...   # idempotent, re-run on pack load
```

Why SQLite-in-the-same-DB and not a new Postgres table or a new file: this is dev/demo-scale metadata (tens of object types, not millions of rows), it needs to be joinable against ledger events (`event_type` should reference `ontology_action_types.id`), and the repo already has exactly one "ops DB" concept (`DMS_OPS_DB` env var, `default_db_path()`) — adding a second database file for ontology would repeat the exact anti-pattern research §7 warns against (a bespoke store per concept instead of one engine).

### 2.3 Action types — this is F8, generalized

`docs/bin/gates/GATE_F8_PACKET.md` scoped a Palantir-style action type (allowlist, compliance, sanitize, sandbox, ledger). **Shipped path today is host `tool_runner`**, not production WASM — see `docs/dms/SANDBOX_ORIENTATION.md`.

**Recommendation: don't build F8 and a separate action-type registry as two efforts.** Define `action_types.yaml` entries for exactly what F8 already scopes (starting with `export_pptx`) plus the ledger event types that already exist and are currently just string literals scattered across `packs/dms/` (`item.intake`, `item.moved`, `agent.run.start`, `agent.tool.*`, `agent.run.done`) so they become one registered, describable list instead of implicit conventions:

```yaml
# packs/dms/ontology/action_types.yaml
action_types:
  - id: item.intake
    description: "Register a new item into a warehouse location."
    object_type: inventory
    required_role: steward
    ledger_event_type: item.intake
    params: [sku, quantity_kg, location_id]
  - id: export_pptx
    description: "Export a chart/table payload to a PowerPoint file."
    object_type: null              # not tied to one object type
    required_role: steward
    ledger_event_type: action.tool_call
    requires_confirm: true          # per GATE_F8_PACKET.md
    params: [payload, output_path]
```

`execute_tool_call_node()` in `CortexOS/execution/dag_runner.py` (the function F8 already names) resolves `node.tool_name` against `ontology_action_types` instead of a bespoke allowlist. Everything else in F8's packet (compliance pre-check, sanitization, sandbox, ledger write) is unchanged — this only replaces "where does the allowlist come from."

### 2.4 Functions — the existing brain/task modules, registered

`packs/dms/generative/brain.py`, `packs/dms/tasks/suggest.py`, `packs/dms/tasks/extract.py` are already Foundry-Function-shaped: typed-ish Python callables that take structured input and return structured output, callable from a DAG node. `functions.yaml` just names them so an agent (or the builder in Phase O6) can discover "what business logic exists to call" the same way it discovers action types, without inventing a new execution model — same lesson as research §7 point 5 (route through primitives that already exist, Apex/Flow-style, not a parallel system).

---

## 3. Codebase knowledge map

The repo already hand-maintains a high-level version of this — `ARCHITECTURE.md` (layers, built-vs-partial table), `CONTEXT.md` (locked decisions), `AGENTS.md` (subagent roles), `STATUS.md` (gate state). Those stay the authoritative human-readable summary. What's missing is a **queryable, file/function-level index** under them, so a coding agent (the `dms-explore` subagent ARCHITECTURE.md §8 already names, or a fresh Claude session) doesn't have to re-grep the whole tree every time to answer "what already implements X" or "what tests cover file Y."

### 3.1 Build it with static analysis, not an LLM

`scripts/build_codebase_ontology.py` (new): walk `CortexOS/`, `packs/`, `netie/`, `tests/` with Python's `ast` module — no model calls, so this is free to run as often as needed and fits the "local-first, 8B model + API fallback" cost discipline (`docs/PONYTAIL.md`) by construction. For each `.py` file, extract: module docstring, top-level `def`/`class` names + first-line docstrings, and the import graph (which modules import which). Write rows into a **separate** SQLite file, `data/codebase_ontology.db` (deliberately not the same file as `data/dms_ops.db` from §2.2 — this is developer/repo metadata with a different lifecycle than customer/demo data; it should be safe to delete and rebuild without touching product data):

- `object_type = "code_module"` — one row per `.py` file, with its docstring and path
- `object_type = "code_function"` — one row per top-level def/class, linked to its module
- `link_type = "imports"` — module A imports module B
- `link_type = "tests"` — best-effort match of `tests/**/test_*.py` files to the module(s) they import, so "what tests cover `packs/dms/audit/ledger.py`" is a link traversal, not a grep
- `link_type = "implements_gate"` — pattern-match test file names against STATUS.md's gate table (`test_f7_rbac.py` → gate `F7`, `test_f1_ledger.py` → gate `F1`) so gate coverage is queryable too

### 3.2 A thin query CLI, not a chat interface

`python -m packs.dms.ontology.query --covers packs/dms/audit/ledger.py` → list of test files. `python -m packs.dms.ontology.query --gate F7` → list of modules + tests implementing that gate. This is deliberately boring and deterministic (SQL against the SQLite file) — the payoff is that ARCHITECTURE.md §8's "Codebase research" subagent pattern gets cheaper and more accurate immediately, with zero new LLM surface area and zero PARKING_LOT tension (this is developer tooling, not a customer-facing feature).

---

## 4. Agent stack layers — orchestrator → agent engine → Agent SDK → builder → manager

Mapping the user's requested five layers onto what exists vs. what's new:

| Layer | Exists today | Gap / what's new |
|---|---|---|
| **Orchestrator** | `CortexOS/execution/dag_runner.py` + `tier_router.py` + `model_router.py` + `CortexOS/routing/cost_ledger.py` (DAG-level, T0–T3 tiers, cost ceiling). **Separately**, `CortexOS/AirGPT/cortex_orchestrator.py` (run queue, thinking-mode, worker thread) — a second, AirGPT-scoped orchestrator, unaware of the first. | These two orchestrators should be explicitly reconciled (see §6 watch item) — not proposing a merge in this plan, just flagging it so nobody builds a third one. |
| **Agent engine** | `CortexOS/fabrication/` — `dsl_parser.py` (`DSLNode`/`NodeType`), `dag_compiler.py`, `skill_registry.py` (`SkillCard`), `skillmesh.py`, `intent_router.py`. Compiles a skill+intent into an executable DAG. Marked "Partial" in ARCHITECTURE.md. | `TOOL_CALL` node kind unimplemented — this is F8/§2.3 above. No other new engine code proposed; finish what exists. |
| **Agent SDK** | Does not exist as a distinct surface. `CortexOS/AirGPT/agent_tools.py`'s `TOOLS` dict is a de facto tool SDK but bypasses compliance/RBAC/ontology entirely. `packs/dms/agents/__init__.py` is an empty placeholder package — reserved but unused. | **New:** `packs/dms/agents/sdk.py` (Phase O4) — the one blessed in-process import surface: `list_object_types()`, `list_action_types()`, `call_action(action_type_id, params, actor)` (routes through compliance + ledger), `query_objects(object_type, filters, actor)` (routes through RBAC + PII redaction). |
| **Agent builder** | `CortexOS/AirGPT/agent_store.create_agent()` — already CRUD for an agent definition (name, kind, skill_pack, tools list, memory_scope). `packs/dms/skills/capture.py` (F6) — captures an approved human action chain as a reusable skill card, which is half of "build an agent from example behavior." | **New:** extend `create_agent()`'s schema with `allowed_object_types` / `allowed_action_types` resolved from the §2 registry instead of the flat `TOOLS` dict (Phase O5), then a genuinely new no-code proposal step (Phase O6) that suggests that scoping from a natural-language use case. |
| **Agent manager** | `cortex_orchestrator.py` (queue/worker) + `agent_store.py` (`agent_runs` table, claim/finish) — a working runtime supervisor, today AirGPT-scoped only. | Not replacing it. Phase O5 makes its tool-execution path call through the new Agent SDK so runs become ledger-audited and compliance-gated the same way a DMS action already is — extends the existing sidecar contract, doesn't replace it. |

---

## 5. FDE workflow: "throw Cortex at a new customer's files, get a precise DMS/app"

Walking the four requested steps against what exists:

1. **"Files of any type"** → this is `PARKING_LOT.md` **P6** ("SQL automation / CSV-Excel ingest pipeline... folder watch, schema infer, AI-proposed cleaning rules, human approve, deterministic apply"), already scoped in `ARCHITECTURE.md` §7 as planned-after-V1. **Do not build a second ingest pipeline.** The only addition this plan proposes for P6, when its condition is met, is: the existing "AI-proposed cleaning rules → human approve" step should *also* propose `object_types.yaml`/`link_types.yaml` entries (§2.1 shape), reviewed the same human-approve-then-deterministic-apply way P6 already handles cleaning rules. That's Phase O8 below, and it is explicitly not scheduled — it's a pointer for when P6 activates, so the two plans don't drift apart.

2. **"Chunk/digest into memory"** → the closest working implementation is `CortexOS/AirGPT/memory_index.py` (`ingest_text`, Chroma-backed `embed_upsert`/`search`, `thin_memories` aging) — but it lives in AirGPT, which is untracked in this repo (per the `airgpt-cortex-sidecar-contract` memory note). Cortex's own hybrid-RAG stack (`CortexOS/rag/indexer.py`, `retriever_dense.py`, `retriever_sparse.py`, `fuser_rrf.py`, `reranker.py`) is the right long-term home but is currently a stub — `indexer.py` still contains a vestigial `ListingDocument`/`chunk_listing` pair from an unrelated real-estate prototype, and ARCHITECTURE.md marks the whole stack "Partial — not wired to demo." **Recommendation, not a phase in this plan:** finishing the existing partial RAG stack is a prerequisite for real "digest unstructured customer files" work, and is its own effort (already tracked as partial in ARCHITECTURE.md, not something this ontology plan should re-scope).

3. **"Generate a precise DMS/app"** → genuinely new. Root `scaffold.py` is a one-time original repo bootstrap (writes `netie/fabrication/__init__.py` etc. — pre-dating the `netie`→`CortexOS` alias in `netie/__init__.py`) and should **not** be extended into a per-customer generator; it's solving a different problem. Phase O7 proposes a new `scripts/new_pack.py` that takes an approved object/link/action-type trio and scaffolds a new pack directory shaped like `packs/dms/` (`security/`, `sql/001_<name>_v0.sql` generated from `object_types.yaml`, `semantic_layer.yaml` generated from the same source for NL→SQL parity, `compliance/<name>_rules_v1.yaml` stub, and `audit/` — note `packs/dms/audit/ledger.py` is **already** pack-agnostic, `repo_root()`/`default_db_path()` don't hardcode "dms", so the new pack can import and reuse it directly rather than copy it).

---

## 6. Phased build plan

Sized ~1–2 days each for a solo developer. Sequenced; each phase's tests must pass before the next starts, matching the existing "ship one, prove it, then the next" discipline in `docs/dms/BUILD_PLAN.md`. Suggested track name **O-series** (Ontology), run alongside the F-series, not replacing it — F7 remainder and F8 keep their own gate status in `STATUS.md` untouched by this plan.

| Phase | Size | Gate status needed to start | What ships | Key files |
|---|---|---|---|---|
| **O0 — Scope decision** | 0.5 day, no code | None | Owner reads this doc, explicitly decides which phases start now vs. wait for the PARKING_LOT P1 condition (§0). If proceeding, update `PARKING_LOT.md` / `STATUS.md` per the repo's own "move out of parking lot" process. | `PARKING_LOT.md`, `STATUS.md` |
| **O1 — Ontology registry schema + loader** | 1–1.5 days | None (pure internal plumbing, transcribes existing `semantic_layer.yaml`) | `packs/dms/ontology/{object_types,link_types,action_types,functions}.yaml` for the *existing* DMS pack only (no new capability — documents what already exists), `packs/dms/ontology/registry.py` loader + SQLite compiler into `data/dms_ops.db`. Regression test asserting registry object/property names match `semantic_layer.yaml`'s `tables:`/`joins:` so the two can't silently drift. | `packs/dms/ontology/*.yaml`, `packs/dms/ontology/registry.py`, `tests/dms/test_ontology_registry.py` |
| **O2 — Codebase knowledge map** | 1 day | None (dev tooling, no product surface) | `scripts/build_codebase_ontology.py` (pure `ast` static analysis, zero LLM calls), writes `data/codebase_ontology.db`, plus `python -m packs.dms.ontology.query` CLI for "what covers file X" / "what implements gate Y" lookups. | `scripts/build_codebase_ontology.py`, `packs/dms/ontology/query.py` |
| **O3 — Action-type registry wired into F8** | 1.5–2 days | **F7 remainder PASS** | This *is* F8 (historical packet `docs/bin/gates/GATE_F8_PACKET.md`), with `execute_tool_call_node()` resolving tools against `ontology_action_types` (§2.3). First action: `export_pptx` via **host shim**. | `CortexOS/execution/dag_runner.py`, `tool_runner.py`, `packs/dms/ontology/action_types.yaml`, … |
| **O4 — Cortex Agent SDK surface** | 1–1.5 days | O1, O3 shipped | `packs/dms/agents/sdk.py` fills the currently-empty `packs/dms/agents/__init__.py` package: `list_object_types()`, `list_action_types()`, `call_action(...)`, `query_objects(...)` — the one blessed in-process import surface for any agent runtime to reach DMS data/actions through compliance + RBAC + ledger. No new HTTP routes required yet. | `packs/dms/agents/sdk.py`, `tests/dms/test_agent_sdk.py` |
| **O5 — Wire AirGPT's agent runtime through the SDK** | 1–2 days | O4 shipped | New entries in `CortexOS/AirGPT/agent_tools.py`'s `TOOLS` dict that call `packs.dms.agents.sdk.call_action(...)` for DMS-scoped actions, audited through the existing `cortex_client.append_ledger` bridge already used by `_audit()`. First AirGPT agent runs that are F1-ledgered and F5-gated. Extends the sidecar contract (`airgpt-cortex-sidecar-contract` memory file, `tests/dms/test_sidecar_routes.py`) — does not replace it. | `CortexOS/AirGPT/agent_tools.py`, `CortexOS/api/sidecar_routes.py` (doc comment only) |
| **O6 — Agent builder v0 (first LLM-touching phase)** | 1.5 days | **Explicit go-ahead per O0** — this is the first phase that starts to resemble the actual P1/AIP-Agent-Studio feature set | Minimal builder: given a pack + natural-language use case, one T2/T3-tier LLM call (respecting `CortexOS/routing/cost_ledger.py`'s ceiling) grounded in the O1 ontology registry + O2 codebase map proposes which object/action types an agent should be scoped to. Output is a human-reviewed YAML agent definition extending `agent_store.create_agent()`'s schema (`allowed_object_types`/`allowed_action_types`) — **never auto-applied**. | `packs/dms/agents/builder.py`, extends `CortexOS/AirGPT/agent_store.py` schema |
| **O7 — New-pack generator (the FDE payoff)** | 2 days | O1, O3 shipped; O6 optional (can hand-author the object/action-type trio instead) | `scripts/new_pack.py`: takes an approved object/link/action-type trio for a new vertical, scaffolds a pack shaped like `packs/dms/` — `security/`, `sql/001_<name>_v0.sql` (DDL generated from `object_types.yaml`), `semantic_layer.yaml` (generated from the same source, for NL→SQL parity), `compliance/<name>_rules_v1.yaml` stub, `audit/` (imports `packs/dms/audit/ledger.py` directly — it's already pack-agnostic). Ship with one worked example: a minimal `packs/crm/` (Account/Contact/Opportunity, informed by the Salesforce UDD research in the companion doc §5) as the smoke test, explicitly **not** as a production CRM feature. | `scripts/new_pack.py`, `packs/crm/*` (demo scaffold only) |
| **O8 — Ingest → ontology handoff** | Not sized/scheduled | **PARKING_LOT P6 condition** ("V1 gated + pilot has dirty imports") | When P6 activates: extend its planned "AI-proposed cleaning rules → human approve" step to also propose `object_types.yaml` entries from customer files, using a finished RAG stack (prerequisite, tracked separately) for unstructured text. Pointer only — do not schedule until P6 does. | `packs/dms/ingest/` (per ARCHITECTURE.md §7, not yet created) |

**Watch item, not a phase:** the two orchestrators (`CortexOS/execution/dag_runner.py` vs. `CortexOS/AirGPT/cortex_orchestrator.py`) should stay explicitly documented as separate-on-purpose (DAG-level vs. AirGPT-run-queue-level) or get reconciled — this plan doesn't propose which, it just flags that a third orchestrator must not get built by accident while doing O5.

---

## 7. What this plan deliberately does not do

- Does not touch F1 (ledger), F5 (compliance engine), or F7 (RBAC) internals — they become the enforcement mechanism the ontology's action types route through, unchanged.
- Does not propose a second ingest pipeline alongside PARKING_LOT P6 — O8 is a handoff point, not a competing effort.
- Does not propose replacing AirGPT's `memory_index.py` or finishing Cortex's RAG stack — both are called out as prerequisites for later work, not scoped here.
- Does not claim "Palantir parity" at any phase through O5 — that framing is explicitly reserved for O6+, which is gated on an explicit owner decision per §0, consistent with how every other PARKING_LOT item in this repo gets promoted.
