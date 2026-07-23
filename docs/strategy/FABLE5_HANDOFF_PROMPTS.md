# Fable 5 Handoff Prompts — Engine-as-SDK / Dual-Brain (Option B via C)

**How to use:** paste **one prompt per fresh Fable 5 session**, in order. Each is self-contained (a cold session). Do **not** skip a gate; each ends with "prove CI green, then stop." Full context: `docs/strategy/ENGINE_SDK_DUAL_BRAIN_PLAN_2026-07-23.md` + `docs/ontology/CORTEX_ONTOLOGY_PLAN.md`.

**Decision recorded (2026-07-23):** Option **B** — shared engine core, keep dual brains — via path **C** (incremental capability landing). Big-bang merge/rebase is rejected.

---

## Global rules (every prompt inherits these)

```
You are a senior engineer on Cortex (D:\Cortex). Read first: STATUS.md, ARCHITECTURE.md,
CONTEXT.md, docs/strategy/ENGINE_SDK_DUAL_BRAIN_PLAN_2026-07-23.md, docs/ontology/CORTEX_ONTOLOGY_PLAN.md.

Non-negotiable guardrails:
- Ship ONE gate per session. Write the tests, make them pass, prove the 3 CI workflows
  (Test, Secrets Scan, RLS Proof) stay green, update STATUS.md, then STOP.
- NEVER big-bang merge/rebase netie-engine into main. Land capabilities one branch + one
  green CI at a time (reconciliation decision = Option B via C).
- Do not touch F1 (ledger) / F5 (compliance) / F7 (RBAC) internals — route through them.
- Do not claim Palantir/AIP parity. Ontology phases past O5 stay gated per PARKING_LOT P1.
- Use native git / PowerShell for git ops on this repo (Git Bash chokes on the D: pack dir).
- Branch off main: git checkout -b <branch>. Open a PR; don't force to main.
```

---

## Prompt 1 — O1: Ontology registry schema + loader  `[no gate needed]`

```
Implement Ontology phase O1 per docs/ontology/CORTEX_ONTOLOGY_PLAN.md §2 and §6.
Branch: feat/o1-ontology-registry.

Scope (this session only):
- Create packs/dms/ontology/{object_types,link_types,action_types,functions}.yaml for the
  EXISTING DMS pack — a transcription of packs/dms/semantic_layer.yaml (tables:/joins:),
  PLUS: primary_key per object, a human description per object type, and an agent_visible
  flag per property (fold packs/dms/semantic_layer.yaml sensitive_columns into agent_visible:false).
- Create packs/dms/ontology/registry.py: loaders (load_object_types/link_types/action_types)
  + compile_to_sqlite(pack_dir, db_path) that writes ontology_* tables INTO the existing
  data/dms_ops.db (same DB as the F1 ledger — not a new file). Plain functions, stdlib sqlite3,
  same style as packs/dms/audit/ledger.py.
- Test tests/dms/test_ontology_registry.py: assert registry object/property names match
  semantic_layer.yaml's tables:/joins: so the two cannot silently drift; assert compile is idempotent.

Done = new tests pass, full pytest + secrets_scan stay green, STATUS.md notes O1 shipped. Then stop.
```

## Prompt 2 — O2: Codebase knowledge map  `[no gate needed]`

```
Implement Ontology phase O2 per docs/ontology/CORTEX_ONTOLOGY_PLAN.md §3.
Branch: feat/o2-codebase-map.

Scope:
- scripts/build_codebase_ontology.py: walk CortexOS/, packs/, netie/, tests/ with Python's ast
  module (ZERO LLM calls). For each .py: module docstring, top-level def/class + first-line docstrings,
  import graph. Write to a SEPARATE data/codebase_ontology.db (NOT dms_ops.db — different lifecycle,
  safe to delete/rebuild). Link types: imports, tests (test_*.py → modules it imports),
  implements_gate (test filename → STATUS.md gate, e.g. test_f7_* → F7).
- packs/dms/ontology/query.py + CLI: `python -m packs.dms.ontology.query --covers <file>` and
  `--gate F7` run plain SQL against codebase_ontology.db.
- Test: build the DB, assert a known file's tests/gate resolve.

Done = tests pass, CI green, STATUS.md updated. Then stop.
```

## Prompt 3 — O3: Action-type registry = F8  `[needs F7 PASS — already green]`

```
Implement Ontology phase O3 (= gate F8) per docs/ontology/CORTEX_ONTOLOGY_PLAN.md §2.3 and
docs/dms/GATE_F8_PACKET.md. Branch: feat/o3-action-types.

Scope:
- packs/dms/ontology/action_types.yaml: register export_pptx (requires_confirm: true) + the
  existing ledger event types currently scattered as string literals (item.intake, item.moved,
  agent.run.start, agent.tool.*, agent.run.done) — register, do NOT rename them.
- In CortexOS/execution/dag_runner.py, make execute_tool_call_node() resolve node.tool_name against
  ontology_action_types (from O1's compiled DB) instead of a bespoke allowlist. Keep F8's existing
  pipeline unchanged: compliance pre-check (ComplianceEngine) → sanitize (injection_guard +
  pii.redact_for_prompt) → sandbox (WasmSandbox) → ledger write.
- packs/dms/compliance/tool_call_rules_v1.yaml + tests/dms/test_tool_call.py: prove export_pptx
  runs through the gate and writes a ledger event; prove an unregistered tool is refused.

Done = TOOL_CALL node no longer raises UnsupportedDAGNodeKind, tests pass, all CI green,
STATUS.md marks F8/O3. Then stop.
```

## Prompt 4 — O4: Cortex Agent SDK surface ("THE SDK")  `[needs O1, O3]`

```
Implement Ontology phase O4 per docs/ontology/CORTEX_ONTOLOGY_PLAN.md §4 (Agent SDK row).
Branch: feat/o4-agent-sdk.

Scope:
- Fill the empty packs/dms/agents/__init__.py by adding packs/dms/agents/sdk.py — the ONE blessed
  in-process surface any agent runtime uses:
    list_object_types(), list_action_types()  → read from O1's compiled registry
    query_objects(object_type, filters, actor) → routes through F7 RBAC + pii redaction
    call_action(action_type_id, params, actor) → routes through F5 compliance + F1 ledger (via O3)
  No new HTTP routes. actor is an api_auth.Caller; enforce agent_visible + role scoping.
- tests/dms/test_agent_sdk.py: a viewer actor cannot query agent_visible:false properties or call a
  steward-only action; a steward can; every call_action leaves a ledger event.

This is "the SDK" in the dual-brain plan. Done = tests pass, CI green, STATUS.md updated. Then stop.
```

## Prompt 5 — Capability landing #1: DuckLake lakehouse (L0)  `[Option B via C]`

```
Land the L0 DuckLake lakehouse capability from netie-engine into main. Branch: feat/land-l0-ducklake.

This is a CAPABILITY LANDING (reconciliation Option B via C) — NOT a merge of netie-engine.
Steps:
- Identify the L0 files: `git diff --stat main netie-engine` and inspect netie-engine's lakehouse
  code (DuckLake / L0 — see its BUILD_PLAN_V2 and docs/research/findings). List the exact paths.
- Port ONLY those files onto this branch off main. For any file that ALSO exists on main
  (e.g. duplicated engine core), pick the CANONICAL version once and delete the other copy — never
  carry two. Reconcile imports against main's current structure.
- Add a gate test proving L0 works end-to-end on main's tree. Wire pytz/duckdb deps already in
  pyproject (dms extra) — confirm no new invalid poetry extra (see the CI-fix history).

Done = L0 tests pass, ALL CI green (Test/Secrets/RLS), STATUS.md notes the landing. Then stop.
Use the "capability-landing template" at the bottom of this doc for the next ones.
```

## Prompt 6 — Capability landing #2: rawknn / hybrid-RAG memory store  `[Option B via C]`

```
Land the rawknn mmap memory store + hybrid-RAG retrieval from netie-engine into main.
Branch: feat/land-rawknn-memory. Follow the capability-landing template (bottom of this doc).

Notes specific to this one:
- netie-engine has the working memory store (CortexOS/memory/store.py is a shared snapshot already;
  the rawknn mmap + retrieval additions are the delta). Cortex's own CortexOS/rag/* on main is a stub
  with a vestigial ListingDocument/chunk_listing from a real-estate prototype — DELETE that vestige
  as part of this landing; do not build around it.
- Keep AirGPT's memory_index.py out of scope (it's untracked per the sidecar contract).

Done = retrieval test passes on main, ALL CI green, STATUS.md updated. Then stop.
```

## Prompt 7 — O5: Wire AirGPT's agent runtime through the SDK  `[needs O4]`

```
Implement Ontology phase O5 per docs/ontology/CORTEX_ONTOLOGY_PLAN.md §4 (Agent manager row).
Branch: feat/o5-airgpt-through-sdk.

Scope:
- Add entries to CortexOS/AirGPT/agent_tools.py TOOLS dict that call packs.dms.agents.sdk.call_action(...)
  for DMS-scoped actions, audited via the existing cortex_client.append_ledger bridge (_audit()).
  First AirGPT agent runs that are F1-ledgered and F5-gated.
- Extend the sidecar contract doc-comment (CortexOS/api/sidecar_routes.py) — do NOT replace the contract.
- tests/dms/test_sidecar_routes.py: an AirGPT tool call that mutates DMS goes through the SDK and
  leaves a ledger event; a tool bypassing the SDK is rejected.

Done = tests pass, CI green, STATUS.md updated. Watch-item: do NOT build a third orchestrator
(dag_runner vs AirGPT/cortex_orchestrator stay as-is). Then stop.
```

## Prompt 8 — Agentic hardening from research (P16)  `[before tool #2 / third-party MCP]`

```
Implement the agentic hardening from docs/strategy/ENGINE_SDK_DUAL_BRAIN_PLAN_2026-07-23.md §4
and PARKING_LOT P16. Branch: feat/agentic-hardening. Small, additive slices — pick the highest-value
first, ship it, stop.

Candidates (do the top 1-2 this session):
- Safety-hook taxonomy over the agent write path: dangerous-action block → injection detect →
  output-secrets scan. Cortex already has scripts/secrets_scan.py + injection_guard + pii.redact;
  add the missing checks + a pre-action hook point in call_action (O4).
- Permission tiers Restrict/Allow/REQUEST: formalize the human-confirm "Request" tier for
  requires_confirm action types (export_pptx already flagged) in the SDK.
- Evals harness (evals/): a minimal fixture-based check that an agent definition only reaches its
  allowed object/action types — REQUIRED before O6 agent builder runs for any paying client.
- Model-routing modes Intelligence/Balance/Cost as an SDK knob over CortexOS/routing/model_router.py
  (Cursor Router pattern).

Done = chosen slice tested, CI green, STATUS.md + PARKING_LOT P16 updated. Then stop.
```

## Prompt 9 — O7: New-pack generator (the FDE payoff)  `[needs O1, O3]`

```
Implement Ontology phase O7 per docs/ontology/CORTEX_ONTOLOGY_PLAN.md §5.3.
Branch: feat/o7-new-pack-generator.

Scope:
- scripts/new_pack.py: takes an approved object/link/action-type trio for a new vertical and scaffolds
  a pack shaped like packs/dms/: security/, sql/001_<name>_v0.sql (DDL generated from object_types.yaml),
  semantic_layer.yaml (generated from the same source for NL→SQL parity), compliance/<name>_rules_v1.yaml
  stub, audit/ importing packs/dms/audit/ledger.py directly (already pack-agnostic — do NOT copy it).
  Do NOT extend root scaffold.py (that's a stale one-time bootstrap for a different problem).
- Ship ONE worked example as the smoke test: minimal packs/crm/ (Account/Contact/Opportunity, per the
  Salesforce UDD research in PALANTIR_AIP_RESEARCH.md §5) — explicitly a demo scaffold, NOT a production CRM.
- Test: generate packs/crm/ from a trio, assert the pack imports + its SQL applies + it reuses the ledger.

This is "companies create the app they want." Done = tests pass, CI green, STATUS.md updated. Then stop.
```

---

## Capability-landing template (reuse for Q1 semantic, Q2 answer engine, S0 streams, orchestration)

```
Land the <CAPABILITY> from netie-engine into main. Branch: feat/land-<capability>.
This is reconciliation Option B via C — a per-capability landing, NOT a merge of netie-engine.

1. Scope it: `git diff --stat main netie-engine`; identify ONLY the files this capability needs.
2. Port those files onto a fresh branch off main. For any file already on main, pick the CANONICAL
   version once and DELETE the duplicate — never carry two copies of an engine component.
3. Reconcile imports/paths against main's current structure. Keep governance routing intact
   (F1/F5/F7). Reach data/actions through packs/dms/agents/sdk.py where applicable.
4. Add a gate test proving the capability works end-to-end on main.
5. Prove ALL THREE CI workflows stay green. Update STATUS.md. Open a PR. Then STOP.

Never land more than one capability per session. netie-engine stays the R&D feeder until its
capabilities are all landed, then it is retired or kept as the ongoing R&D branch.
```
