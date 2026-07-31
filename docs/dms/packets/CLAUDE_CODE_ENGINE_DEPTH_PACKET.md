# Claude Code — Engine Depth Packet (long horizon)

**Paste this whole file as the session brief ONLY after the DMS floor in
`docs/dms/DMS_ANCHORED_SEQUENCE.md` (C7-prod, claim review, Postgres→Amend→Spaces)
or when pulling a single H slice that directly improves live DMS.**

**Default Claude Code start:** `CLAUDE_CODE_HANDOFF_NEXT.md` Prompt I → J, then Postgres/Amend/Spaces — not H0-A.

**Session start (every wave):**

```text
python D:\Netie-KB\scripts\kb.py search "<wave keywords>"
# Report 3 lines: rules / workflow / attacks
Read skill_distill/DISTILL.md + learned/INDEX.md for the wave topic
PREFLIGHT docs/subagents_findings/INDEX.md → HIT|PARTIAL|MISS
```

**Session end (every wave):**

```text
python D:\Netie-KB\scripts\kb.py new finding --title "..." --tags "..." --severity high
# Fill expected/actual/repro/CLASS/R#
Update docs/subagents_findings/YYYY-MM-DD_<topic>.md + INDEX.md
If reusable loop: kb.py new workflow (or promote finding)
Commit ONLY if owner asked — explicit paths
```

---

## Wave order (do not skip exit gates)

```
H0 floor → H1 ontology spine → H2 DB/DMS supremacy
                ↘ H4 Act/Click (after H1.4)
H3 Distill/KB/agentic OS (can overlap H2 carefully)
H5 P17/P18 packaging
H6 AIP depth (STATUS gate)
```

---

## WAVE H0 — Floor freeze + brainstorm charter

### Prompt H0-A — Brainstorm charter (read-only, then write charter doc)

```text
Repo: D:\Cortex. READ ONLY for code. You may write ONE markdown:
docs/strategy/charters/H0_ENGINE_CHARTER_YYYY-MM-DD.md

Mission: Set the tone for Netie as the strongest governed engine for general
agentic tasks and especially DB/DMS + Act. Read fully:
- docs/strategy/NETIE_ENGINE_DEPTH_PLAN_2026-07-31.md
- docs/strategy/CORTEX_FINAL_GOAL.md
- docs/ontology/CORTEX_ONTOLOGY_PLAN.md (§0 gate tension)
- docs/ontology/PALANTIR_AIP_RESEARCH.md (skim)
- PARKING_LOT.md P1, P12, P14, P17, P19, P21, P22
- skill_distill/learned/INDEX.md
- D:\Netie-KB INDEX.md (python scripts/kb.py index if stale)

Produce a charter with:
1. Non-negotiable laws (from KB R-#### + CLAUDE.md)
2. What "Palantir-level ontology" means INTERNAL vs MARKETING
3. What "Snowflake/Databricks foundation" means as ENGINE capabilities only
4. What "Click revolution" means (ontology action → Act → OV → ledger)
5. Distill/KB operating system for every future wave
6. 10 risks that would make Netie weaker if we chase them
7. Recommended first CODE wave (must be H1.1 audit unless you justify)

Do not start coding. End with kb finding summarizing the charter decision.
```

### Prompt H0-B — Commit / merge hygiene (only if owner says commit)

```text
Owner asked to commit. Stage EXPLICIT paths only for:
- drillthrough fix, exclusion clarify, C5, C8, samples, docs plans
Never git add -A. Separate commits: engine fixes / ontology-c5-c8 / docs.
Land D:\DMS worktree predictive + drillthrough gate if still isolated.
```

---

## WAVE H1 — Ontology refresh (Palantir-shaped spine)

### Prompt H1-1 — Ontology audit (brainstorm + report, then fix drift)

```text
Repo: D:\Cortex. Pack=dms.

1) Inventory every fragment that claims "what exists":
   packs/dms/ontology/*.yaml
   packs/dms/semantic/metrics.yaml + semantic_layer if present
   packs/dms/compliance/*
   ledger event type strings in packs/dms and CortexOS
   Agent SDK allowed tools
   /dms/ontology API response

2) Write docs/ontology/AUDIT_YYYY-MM-DD.md:
   - Coverage matrix: warehouse table → object_type → metrics → actions → ledger events
   - Drift list (semantic vs ontology vs code)
   - Priority fix list for H1.2–H1.5

3) Do NOT invent Foundry UI. Do NOT expand _C2_ALLOWLIST.

Exit gate: audit committed to docs/ontology/; kb finding filed.
```

### Prompt H1-2 — Refresh ontology YAML to full warehouse + actions

```text
Repo: D:\Cortex. Implement H1.2–H1.4 from NETIE_ENGINE_DEPTH_PLAN.

- Every warehouse table → object_type with PK, description, agent_visible props
- link_types cover real joins used by metrics
- action_types: every mutate + export + agent tool; ToolClass set (C5)
- functions.yaml: business logic callables agents may invoke
- Sensitive columns ONLY via agent_visible:false (single source)
- Recompile registry; tests for load + /dms/ontology counts
- Agent SDK: refuse unregistered tools

Verify: pytest ontology/agent tests; lint-imports; live GET /dms/ontology
Exit: coverage matrix rows all green for DMS pack.
```

### Prompt H1-3 — Ontology graph read API + lineage on metrics

```text
Additive contract minor if needed (bump version.py + pyproject + export_openapi).

- GET graph: nodes (objects) + edges (links) for Library Data Map
- metrics.yaml gains reads: [object_ids] (replace SQL regex heuristic in ontology_routes)
- T7: prefer real provenance cols; approximate flag honest when absent

Assert customer envelope still E1–E8. No hand-edited contract JSON.
```

### Prompt H1-4 — Brainstorm: AIP Agent Studio patterns WITHOUT building Studio

```text
Read-only brainstorm → docs/ontology/AIP_PATTERNS_FOR_NETIE.md

Map Foundry/AIP concepts → Netie primitives already shipped or in H1.
List which UI belongs in D:\DMS vs engine API.
Propose 5 agent "jobs" expressible ONLY as sequences of call_action + query_objects.
Stop. No code.
```

---

## WAVE H2 — DB / DMS supremacy

### Prompt H2-1 — C7 schema-gate product hardening

```text
Repo: D:\Cortex. sql_generation_port Protocol already exists.

Harden product path: schema retrieval → generate → sqlglot → EXPLAIN →
bounded retry → abstain. assert_envelope_valid on live path.
Exclusion/clarify must never fall through to query-skill.
Re-run: bench.corpus, paraphrase, live_probe.
wrong must stay 0. Document robustness delta.

Exit: W7 notes in docs/eval/ + kb finding.
```

### Prompt H2-2 — Generalize clarify loop (entity resolution UX)

```text
Generalize exclusion-clarify to entity-clarify:
locations, suppliers, statuses, SKU names.
Engine returns suggestions Yes/No; DMS chip 5s timeout pattern reused.
Contract: prefer suggestions[] + assumptions; additive clarify_options only if required (minor).

Corpus cases for ambiguous entities. Live demo script updated.
```

### Prompt H2-3 — Postgres Phase0 topology for host DMS

```text
Read docs/strategy/DMS_SPACES_PRODUCT_2026-07-29.md build order.
compose-postgres is healthy but not on host — fix topology OR document Caddy-only
path so host-run DMS gets DATABASE_URL without displacing :8010/:8090 bench stack.

Exit: database_configured true on demo host; RLS smoke; do not break live ask.
Then H2.4 Amend + H2.5 Spaces persist (separate prompts — do not batch).
```

### Prompt H2-4 — Amend Proposal (engine actions + DMS)

```text
Amend = versioned Proposal → confirm token → apply → hash receipt → rollback.
Engine: action_types + call_action only. DMS: UX. Excel source-only.
compliance_gate before side effects. Tests on envelope + ledger.
```

### Prompt H2-5 — Spaces persist + enforce

```text
Retire in-memory Spaces stub. Persist members + source attach.
Data-plane intersection (not UI hide). No company-wide leak.
Hard scenario sketch for 3GB later (W11) — do not build 3GB yet.
```

### Prompt H2-6 — claim_n to 310

```text
python -m bench.verify_gold --review --by <name>
Only gold_verified counts. Trust supported stays false until 310 + wrong==0.
```

### Prompt H2-7 — BIRD three-bucket (after H2.5)

```text
bench/bird/ adapter; correct | abstained | incorrect; schema-width sweep;
assert_envelope_valid. Never collapse abstain into incorrect.
```

---

## WAVE H3 — Distill + KB → workflows/skills → agentic OS

### Prompt H3-1 — Mine distill + Claude tasks into KB (unverified)

```text
Read every skill_distill/captures/*.md (skip _TEMPLATE).
Read C:\Users\OoiJianHong\.claude\tasks\ if present (TASKS_INDEX).
Extract findings → kb.py new finding status unverified; origin cites distill: path or task:uuid.
Write D:\Netie-KB\docs\MINING_REPORT.md: transcripts, counts, promote candidates.
Do NOT promote to active rules yourself.
Expect 15–40 findings. Low yield OK.
```

### Prompt H3-2 — Promote workflows (loop engineering)

```text
Create W-0005+ with FULL sections (Trigger Shape Rationale Steps Anti-patterns Model tier Cost Proven on):
- W-0005 Entity-clarify confirm loop (from exclusion clarify)
- W-0006 Distill-capture → promote → skill
- W-0007 Ontology-refresh audit → YAML → compile → agent gate
- W-0008 Amend propose → confirm → apply → receipt
- W-0009 Act click: call_action → computer_control → OV → ledger
- W-0010 Corpus-first eval expand (extend W-0004)

kb.py index && render && sync_agents.py
```

### Prompt H3-3 — Package skills S-####

```text
From promoted workflows, create Netie-KB/skills/*/SKILL.md (Claude Code skill format):
- subagent-preflight (may already exist in ~/.claude — canonicalize into KB)
- entity-clarify
- adversarial-review (from W-0001)
- ontology-compile-check
sync_agents.py must copy to ~/.claude/skills with divergence guard.
```

### Prompt H3-4 — Wire workflows into Seek/OSR

```text
When Seek/OSR plans work, attach matching W-#### id + Model tier hints.
UI (AirGPT/DMS) may show "follow W-0005". Engine returns structured plan metadata.
Tests: known question shapes retrieve expected workflow id from KB search helper.
```

### Prompt H3-5 — Memory / context assembly depth

```text
Read distill RAG captures. Design (then implement min):
- ontology slice in context
- retrieval citations
- ledger snippets for prior actions
Honesty: no MemPalace/JEPA trained claims.
Park full multi-pool broker behind design doc if >3 days.
```

### Prompt H3-6 — Brainstorm multi-agent orchestrator policy

```text
Write docs/engine/AGENTIC_ORCHESTRATION_POLICY.md from Anthropic distill captures.
Bless: orchestrator-centric, isolated adversaries, separate verifier (R-0003, W-0001).
Forbid: default planner/coder/tester swarms; shared-context adversary collapse.
Then implement ONE policy check in tool_runner or agent team launcher.
```

---

## WAVE H4 — Click / Act revolution

### Prompt H4-1 — Ontology actions for enterprise clicks

```text
Define action_types for: export_csv_open, highlight_excel_range, browser_fill_form,
approve_amend, open_source_panel (as applicable).
Each: ToolClass, RBAC role, OV gate requirement, ledger event.
Bridge call_action → computer_control for Pointer.
Fail-closed if OV denies. Tests with mock Act backend.
```

### Prompt H4-2 — End-to-end demo: ask → clarify → answer → Act

```text
Script + automated smoke:
1) remove wolf from top 5
2) Yes exclude
3) Answer with sources
4) Act chip: open contributing rows in Excel OR copy governed CSV
Record envelope assertions at every step.
Document in docs/dms/DEMO_ACT_LOOP.md
```

### Prompt H4-3 — Red-team Act (W-0001)

```text
N adversaries, separate verifier, judge.
Lenses: silent Act, OV bypass, wrong window, prompt injection via Excel cell.
Add corpus cases. Fix root cause class. Re-run.
```

---

## WAVE H5 — Engine as product

### Prompt H5-1 — API surface catalog

```text
Catalog every engine HTTP surface a builder needs.
Draft docs/engine/API_SURFACES.md (P18). Note contract versions.
No second brain. DMS remains HTTP consumer example.
```

### Prompt H5-2 — Self-host smoke

```text
One script: start Cortex pack=dms with OV optional; run health + ask fixture.
Document in docs/engine/SELF_HOST.md. G2.6 stays blocked on OpenVault clean lane.
```

---

## WAVE H6 — AIP depth (only after owner STATUS edit)

```text
IF AND ONLY IF STATUS.md explicitly unparks P1 for build:
Implement Agent Studio patterns as ENGINE APIs + DMS Library explorers.
Otherwise: keep docs/ontology/AIP_PATTERNS_FOR_NETIE.md as the ceiling.
```

---

## Parallel Cursor lane (not Claude Code)

- Exclusion/Act chips polish, Ontology/Trust/Library Data Map UI
- Spaces UX once H2.5 persists
- Visual demo recording

Claude Code owns: ontology YAML, Protocols, gates, eval adapters, KB/distill, Act bridge, Postgres topology.

---

## Definition of done for "foundation set"

When all of the following are true, the tone is set and Netie can claim a
*governed agentic DB engine foundation* (still not "we are Palantir"):

1. H1 coverage matrix 100% for DMS pack  
2. Agents cannot write outside call_action  
3. ≥12 KB workflows with Model tier  
4. Distill 2026-07 captures triaged into KB  
5. claim_n ≥310 and wrong==0  
6. One Act loop green through OV + ledger  
7. C7 product path green on paraphrase  
8. Spaces persist + amend on real Postgres  

Until then: build hard, ship honesty, file findings.
