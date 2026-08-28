# Agentic Loop Capability PRD

**Status:** PLAN (PARKING_LOT **P23** / G3) · **Date:** 2026-08-25  
**Owner:** Cortex engine (not a vertical pack)  
**Companions:** [`CORTEX_WHITEPAPER.md`](CORTEX_WHITEPAPER.md) · [`PRODUCT_ROLES.md`](../../PRODUCT_ROLES.md) · [`ENTERPRISE_GEN_CFSM_LOOP_PLAN.md`](ENTERPRISE_GEN_CFSM_LOOP_PLAN.md) · [`CORTEX_ONTOLOGY_PLAN.md`](../ontology/CORTEX_ONTOLOGY_PLAN.md)

**distill:** `skill_distill/captures/2026-08-25_cursor_cloud-agent-loop.md`  
**distill:** `skill_distill/captures/2026-07-25_claude-code_all-lanes.md`  
**distill:** `skill_distill/captures/2026-07-25_cursor_model-routing-multitask.md`

> Do not present this document as shipped. Nothing below is built unless a row in §3 says **Shipped**.

---

## 0. Verdict (read this first)

**Cortex is not as strong as the Cursor engine or Claude Code as a general agentic loop today.** Connecting an app to Cortex over HTTP does **not** give you a Cursor-class coding/action agent. It gives you a **governed** engine: DAG, cost, ledger, abstain, OSR, seeker, workflow subagents with a 4-step JSON-in-text tool loop.

Cortex **is** stronger than Cursor/Claude at: fail-closed reads, ontology-gated writes, cost ceilings, untrusted-payload wrap, and "do not invent numbers the warehouse does not have."

**Use Netie/DMS when the job is governed data.** Use Crew when the job is multi-step chat/spawn. Use Pointer only as an Act client. Use Constructor for canvas workflows/ontology sketches. Use OpenVault for keys + FreeRoute + ship. Close the loop gap in **G3** (this PRD). Do **not** grow a third orchestrator inside Crew to fake Cursor.

---

## 1. Ecosystem map (repos + apps)

Two planes. Do not mix them.

| Plane | What it is | SoT |
|---|---|---|
| **P3 Cortex** | Engine: think, route architecture, execute under ledger | this repo `github.com/Netie-AI/Cortex` |
| **P2 OpenVault** | Custody: keys, FreeRoute, leave-machine/deploy gate | `github.com/Netie-AI/OpenVault` (:5000) |
| **P4 skins** | Chat, clicks, canvas, launch board | sibling products; they **ask** Cortex |

### 1.1 GitHub org `Netie-AI` (this token can see)

| Repo | Role in the AI product |
|---|---|
| **Cortex** (private) | The engine. DMS pack lives here as the reference consumer. |
| **OpenVault** | Keys + FreeRoute + ship. Not the agent loop. |
| **constructor** | Netie-native workflow canvas. Not n8n. Skin over Cortex. |
| Cassandra, VKing, OpenForge, AnalogCrawler, OpenHBM, CI-Doctor, AIM, Vertex | Adjacent / hardware / other products. **Not** the agentic loop. Do not fold them into Cortex. |

A GitHub 404 from a cloud token is **not** proof a repo is missing. Operator-confirmed **private** products this token cannot list: `dms`, `Netie-KB`, `Netie`, `Pointer`, `landing`, `Space`, `netie-control`, `RUMA-Houser`, `ViKing`. AirGPT / chatbot / OMI were not in that screenshot and stay unseen here.

cite: `docs/subagents_findings/2026-08-25_crew-ship-gate-estate.md`

### 1.2 Apps (how many, what each does)

Count **product surfaces**, not git remotes. Active AI estate:

| # | App | Job | Talks to Cortex how | Honest state |
|---|-----|-----|---------------------|--------------|
| 1 | **Cortex** | Governed agentic engine | is the engine | Strong governance; weak general ReAct (this PRD) |
| 2 | **OpenVault** | Keys, FreeRoute (best model under budget), ship/deploy | Cortex asks; OV allows | Live :5000; G2.6 still blocked on P17a |
| 3 | **AirGPT** | ChatGPT-layer host shell (phone, pairing, apps hub) | Sidecar + Agent SDK; must stay thin | Sibling; `CortexOS/AirGPT/` may be a working mirror, not SoT |
| 4 | **DMS / Spaces** | GPT-for-database + Excel swamp; warehouse ops; ontology in progress | `/dms/query`, pack routes, envelope | Answer plane shipped; Palantir-class ontology still P1 |
| 5 | **Constructor** | Homemade n8n: chat compiles a canvas; ghost dry-run; ontology sketch | `/cortex/constructor/`, `GET /api/connectors` | Builtin skin + public `constructor` repo. Default graph: connector -> ontology -> insight -> foundry -> app. Do not clone n8n. |
| 6 | **Cortex Crew** | Grok-bot agentic chat. Manager + spawn + A2A | `cortex_ask` + engine bridge; models via OV | Standalone :8020; golden worktree `D:\Cortex-crew`. Sustainable **if** it stays a skin (see §7). |
| 7 | **Pointer / Netie Clicks** | Computer-control Act client | Cortex Act on :8010, fail-closed | Human-confirm. Not a second SendInput next to UACC. |
| 8 | **Netie Control / Plane** | Display + launch + estate status; fire up apps | Watchdog launch-only; does not think | :8099 board. Keep-alive, not the loop. |
| 9 | **Netie-KB + skill_distill** | Distill Claude/Cursor skills into Netie | Captures in this repo; KB sibling | Process lives; auto skill-pick on the live engine path does **not**. |
| 10 | **OpenIDE** | Coding expert slice | Asks Cortex for brain/tools | Not the deploy console. |
| 11 | **DMS Spaces / Netie Space** | ACL-scoped sandbox over selected sources | Same engine + lake/query | Product locked; ACL not full. |

**Local ports (typical):** Cortex API 8000 / engine 8010 / DMS UI 3000 / OpenVault 5000 / AirGPT 8765 / Crew 8020 / Plane 8099.

### 1.3 Safe path (non-negotiable)

```
App (AirGPT / DMS / Crew / Constructor / Pointer / OpenIDE)
  -> Cortex (classify turn, pick preset, assemble context, run loop)
    -> OpenVault (keys + FreeRoute + may this leave / deploy?)
      -> tools / models / ship
    <- only what passed the gate
  <- ledger / write gate / abstain
```

Omni-retrieve or leave-machine **without** OpenVault = unsafe. Cortex thinks. OpenVault custodians. Crew/AirGPT must not grow a second vault.

---

## 2. How Cortex works today (honest)

### 2.1 Paths that already exist

| Path | Entry | What it is | Agentic? |
|---|---|---|---|
| DMS answer | `POST /dms/query` | Semantic layer -> SQL -> DuckDB. Abstain-first. | **No.** Router + generation, not a tool loop. |
| Workflow DAG | `/api/workflows*` | Templates (research / audit / review / docs) -> AGENT_TASK nodes | **Bounded yes.** Default `max_steps=4`. Tools via JSON-in-text, not native function calling. |
| OSR + gen-cFSM | `/fire`, `/api/engine/osr` | known / near / open -> reuse winner / race / generate DAG | **Reactive interrupt.** Not a chat ReAct loop. |
| Seeker | routines tick | Proactive next action toward bound `EnterpriseGoal` | **Goal-seeking.** Confirm-gated. JEPA is a 64-dim **family proxy**, not a trained JEPA. |
| Crew Manager | `:8020` | Charter + `spawn_agent` + native `tool_choice` via OpenVault | **Closest to Cursor chat.** Keyword `detect.py`, not LLM skill search. |
| Constructor desk | `GET /api/connectors` | Dispatch agents into workspaces / Cursor chats | Orchestration **desk**, not the model loop. |
| Discovery | `find_skills` / `find_mcp` / `find_subagents` | Catalog search (416+ skill refs) | **Available**, not auto-called at turn start. |

### 2.2 Prompts that exist (and where they do not)

| Surface | Prompt | Strength |
|---|---|---|
| `execution/prompt_library.py` | Specialist subagent prompts (research plan/search/verify/synth, audit, review, docs). User override via `data/prompts/<id>.md`. | **Strong** for workflow templates. Not injected into DMS query or a generic `/v1/chat`. |
| `crew/runtime.py` `MANAGER_CHARTER` | How to answer vs `cortex_ask` vs spawn vs verify vs OpenVault. | **Strongest live system prompt** in the estate. Crew-scoped only. |
| `execution/agent_task.py` | `node.system` + "reply with ONLY this JSON tool call". | **Weak** vs Cursor: 4 steps, JSON-in-text, no mode/citation/anti-jailbreak/skill loader. |
| `personality/tone.py` `compose_system_prompt` | Tone profile. | Not an agent loop. |
| Architecture presets | `minimal` default | Catalog exists; **no turn classifier** picks `dag` vs `rag` vs `computer_control` for a chat message. |
| `routing/judgment_model.py` | Keyword/heuristic tier pick (legal terms, birthday, context size). | **Not** Cursor-class model hierarchy (explore=fast, hard=frontier, no silent substitute). |

### 2.3 What an API consumer gets **right now**

If DMS, AirGPT, Pointer, or Constructor call Cortex:

1. **Governed Q&A** works (abstain, badge, audit_id) when the semantic layer covers the question.
2. **A named workflow template** can fan out research/audit subagents with specialist prompts.
3. **They do not get:** Cursor Agent/Plan/Ask/Debug, native 20+ tool ReAct, automatic "which skill?" then "no skill -> research -> generate skill", content-aware communication rules, BFS over goals, or a frontier-vs-fast model split per subagent.

So: **Cortex is suitable to power DMS for database work today.** It is **not** suitable as a drop-in Cursor/Claude replacement for "build anything" agentic action. Crew is the chat-shaped bet; it still needs G3 to stay honest when models change.

---

## 3. Capability matrix (Cursor / Claude Code / Cortex)

| Capability | Cursor (live 2026-08-25 cloud agent) | Claude Code (2026-07-25 capture) | Cortex today | Gap owner |
|---|---|---|---|---|
| Turn modes | Agent / Plan / Debug / Ask with write bounds | Plan -> task list -> execute; permission modes | Architecture presets; default `minimal`; no turn switch | G3.1 |
| System prompt | Large: communication, citations, skills, anti-jailbreak, tools, goals | CLAUDE.md + auto-memory + hooks | Specialist library + Crew charter; AGENT_TASK is thin | G3.0 |
| Tool calling | Native function tools; lazy MCP (`GetDynamicTools` then `CallDynamicTool`) | Deferred ToolSearch; native tools | AGENT_TASK JSON-in-text, max 4; Crew native via OV | G3.2 |
| Skill pick | Skills listed with "read when relevant"; user/project/agent skills | SKILL.md + find-skills | `find_skills` catalog; Crew keyword `detect.py`; **not** on DMS path | G3.3 |
| Miss -> generate | Human/agent writes SKILL.md | skill-creator | SkillOpt `evolve=` optional, off by default | G3.3 |
| Subagents | `Task` parallel; child isolated; parent must pass full brief; summary return | Frontmatter agents; depth cap; sanitize | DAG fan-out + Crew spawn; depth gate shipped | G3.6 (wire, don't rebuild) |
| Model route | Picker + agent YAML + Task allowlist; no silent substitute; explore=faster | Per-agent model/effort | T0-T3 heuristics + OV FreeRoute `auto` | G3.4 |
| Goal identify | User query + rules; optional CreateGoal | Plan mode | `EnterpriseGoal` + seeker + OSR | G3.5 (connect to chat turn) |
| BFS / search | Agentic Grep/Glob/Read, not silent blob RAG | Same | Catalog BM25-ish `CatalogIndex`; code search is not the default loop | G3.5 |
| JEPA / value | None (product agent) | None | Family cosine **proxy**; trained JEPA parked (honesty) | Keep proxy; do not claim trained |
| Governance | Approval cards + modes | 6-step permission pipeline + sandbox | F1/F5/F7 + untrusted wrap + confirm_required | **Keep Cortex lead** |
| Computer control | Browser MCP / cloud VM | OS sandbox | Probe, default OFF | Stay fail-closed |

---

## 4. Incapability to solve (the product requirement)

These are the holes that make "Cortex as Cursor" fail in real use. Each has an acceptance test. Build in order. One slice per PR.

### G3.0 -- System prompt compiler

**Need:** One function that, given `{mode, goal, skills[], tools[], content_class, actor}`, emits the system prompt for **that** turn.

Modes at minimum:

- `answer` -- non-agentic: DMS/query, citations, abstain, no tool loop
- `agentic` -- tool loop, skills, subagent spawn rules, confirm-gated writes
- `plan` -- read-only, produce a DAG/workflow spec, do not execute
- `computer_control` -- Pointer/Act; fail-closed unless armed
- `abstain` -- engine cannot prove safety or coverage

**Must include (distilled from Cursor, inverted for governance):**

1. Communication: lead with the answer; ASCII; no fake "I ran tests" without evidence.
2. Content awareness: agentic vs Q&A vs refuse; do not run tools on a lookup question.
3. Skills: "if a listed skill matches, read it before acting; if none match, call `find_skills`; if still none, propose a generated skill (human approve), do not silently invent a playbook."
4. Tools: native schemas; lazy MCP; never dump 400 skill cards into context.
5. Subagents: full brief, isolated context, summary-only return, depth gate (already shipped).
6. Anti-injection: keep untrusted wrap; do not weaken `manifest.py`.
7. Model: this subagent's allowlisted model; **no silent substitute**.

**Not:** a second Crew charter copy-pasted into DMS. One compiler, many skins.

**Accept:** A unit test that (a) `answer` mode prompt contains abstain/badge rules and **zero** spawn_agent, (b) `agentic` mode contains find_skills + max-steps/governor, (c) switching mode changes the prompt bytes. DMS query stays on `answer`.

### G3.1 -- Turn classifier (agentic vs not)

**Need:** Before the model loop, classify the user turn:

`lookup | generate_content | agentic_action | plan_only | computer_control | refuse`

Route:

- lookup -> DMS/answer or RAG (`answer` prompt)
- generate_content -> single completion, no tools
- agentic_action -> G3.2 loop
- plan_only -> gen-cFSM / workflow spec, no execute
- computer_control -> Pointer path, fail-closed
- refuse -> abstain envelope (customer badge, not a green success)

**Accept:** Golden paraphrases: "top 3 SKUs by qty" never opens AGENT_TASK; "open a PR that adds X after tests pass" never goes to `/dms/query` as if it were a metric.

### G3.2 -- Native tool-calling loop (unify the two loops)

Today: AGENT_TASK parses `{"tool":...}` from text, **4 steps**. Crew already sends OpenVault `tool_choice=auto` and parses `tool_calls`.

**Need:** One engine loop used by Crew **and** workflow AGENT_TASK:

- Native function calling when the provider supports it; JSON-in-text fallback only.
- Governor: max steps, cost ceiling, depth, confirm_required (already in agent_sdk).
- Raise default max_steps for agentic mode (e.g. 16) **with** the same cost ledger; keep 4 for cheap workflow nodes unless annotated.

**Accept:** A research template node completes with native tool_calls (mocked adapter) and still records step_journal. A broken journal still cannot fail the run (existing invariant).

### G3.3 -- Skill select or research-generate

**Need:** On `agentic` turns:

1. `find_skills(goal)` (already exists).
2. If best score >= tau, inject **that** skill body (lazy, not the whole catalog).
3. If miss: run the **existing** research workflow template (plan -> search -> verify -> synth) then propose `skills/<id>.yaml` or a Crew skill pack. **Human approve.** SkillOpt evolve stays optional behind a flag.

Crew `detect.py` keyword cues stay as a **cheap prior**, not the SoT. LLM + catalog outrank regex.

**Accept:** Goal "playwright e2e" retrieves the playwright skill without the user naming the file. Goal "invent a brand-new warehouse slotting playbook" does not pretend a local skill exists; it returns a proposal, not a fake execution.

### G3.4 -- Model hierarchy through OpenVault

**Need:** Cortex picks **role**, OpenVault picks **route**.

| Role | Default | Never |
|---|---|---|
| classify / embed | T0 | frontier |
| explore / grep-like subagent | fast / cheap | silent upgrade to opus |
| hard coding / hostile-SQL / compliance | frontier (T3) | grok-fast |
| DMS lookup | local/T1 if possible | paid frontier unless L2 needs it |

Rules (from Cursor distill): Task `model` must be allowlisted; **no silent substitute**. Crew already rewrites grok-fast -> grok-4.6. Lift that into the engine router, do not leave it as a Crew-only string replace.

**Accept:** A test that a request pinned to `qwen2.5-7b` does not come back as `claude-sonnet` because JudgmentModel felt "legal". FreeRoute `auto` is explicit in telemetry, not hidden.

### G3.5 -- Goal identify + loop vs goal engineering

Do **not** replace gen-cFSM with free ReAct as source of truth.

| Case | Engine |
|---|---|
| Known family + proven winner | OSR `known` -> stored DAG/preset |
| Near | race top-3 (exists) |
| Open | gen-cFSM finite DAG (exists) |
| Bound enterprise goal + idle | seeker (exists) |
| Chat turn with no bound goal | G3.1 classifier; if agentic, **identify goal** (one sentence + measurable done) then compile DAG |

BFS: use it for **code/repo search** (Grep/Glob analogue already in Cursor; Cortex should expose discovery + filesystem tools to agentic mode), not as a second planner. JEPA stays the **family/value proxy** until a trained model exists. Honesty: do not demo "JEPA world model."

**Accept:** Silence litmus still holds (G2.1). A novel chat task emits `goal_id` + `dag_hash` + predicates before tools that write.

### G3.6 -- Parallel subagents (wire, don't rebuild)

Already shipped: DAG phases, Crew spawn, depth gate, sanitize, parent-must-brief (workflow templates).

**Need:** The **chat** path (Crew + future `/v1/agent`) uses the same contract as Cursor `Task`:

- Parent writes the entire brief (child does not inherit history).
- Child returns summary only.
- Parallel siblings share parent depth, not each other's.
- Verify is a **different** agent with criteria (Crew already has this; keep it).

**Accept:** Existing `test_distill_engine_improvements.py` plus one Crew integration test that two siblings cannot see each other's transcript.

---

## 5. Product routing (when Cortex is in the middle)

| User need | Correct surface | Wrong surface |
|---|---|---|
| "What is on-hand for SKU-BETA?" | DMS -> Cortex `answer` | Crew guessing numbers; Pointer clicking Excel |
| "Build a workflow that drafts a PO when stock < reorder" | Constructor canvas -> Cortex DAG; human Maximize | LangGraph / n8n clone inside CortexOS |
| "Click the WMS and dump the grid" | Pointer Act -> Cortex computer_control, confirm | UACC in the engine process; Crew overnight cron |
| "Chat like Grok, spawn a reviewer" | Crew | DMS query; a new LangGraph |
| "Which model, which key, may this leave the box?" | OpenVault FreeRoute + gate | AirGPT env.local as a second vault |
| "Is Crew up? Start OpenVault." | Netie Control / Plane watchdog | Crew implementing tickets in the 15-min cron |
| "Learn how Cursor loads skills" | Netie-KB + `skill_distill` | Parking a 'review this capture' with no ingest |
| "Ontology like Palantir" | Constructor sketch + packs/dms/ontology plan (P1/O-series) | Claiming AIP parity in sales |

**Pointer scenario:** Cortex **is** needed: Act fail-closed, OSR after plan, ledger. Pointer is not the brain.

**DMS + different models:** suitable **if** G3.1 keeps lookup on `answer` and G3.4 does not send warehouse questions to a chatty frontier that ignores sqlglot. Without G3.0/G3.1, swapping models **will** skip skills and invent SQL. That is the bug this PRD exists to prevent.

---

## 6. Cortex Crew -- can it be sustainable?

**Yes, as a skin.** No, as a second engine.

Keep:

- Manager charter that **must** `cortex_ask` for governed numbers
- Models only through OpenVault
- Detect + skill pass-down (already shipped)
- Computer control default OFF
- Isolated worktree for UI experiments; engine APIs land on Cortex `main`

Kill / never:

- Crew implementing tickets in estate cron
- Crew copying API keys into `data/crew/keys.json` as SoT
- LangGraph inside Crew
- Infinite Cursor cloud swarm

After G3.0-G3.2, Crew should call the **engine** prompt compiler + native loop instead of a private ReAct. That is what makes Crew survivable when you swap grok-4.6 for Claude or a local Qwen.

---

## 7. What this PRD does not do

- Does not add LangGraph, CrewAI, n8n, or a third orchestrator.
- Does not weaken `execution/manifest.py` or reclassify hostile SQL.
- Does not claim trained JEPA, MemPalace, or Palantir AIP parity.
- Does not merge `D:\Cortex-crew` into this checkout.
- Does not implement G3 in this document's landing PR -- **plan only**.

---

## 8. Build order and gates

| Slice | Depends | Test gate |
|---|---|---|
| G3.0 prompt compiler | none | prompt-bytes tests in `tests/test_execution/` |
| G3.1 turn classifier | G3.0 | paraphrase set: lookup vs action vs refuse |
| G3.2 native tool loop | G3.0 | mock adapter tool_calls + step_journal |
| G3.3 skill select/generate | G3.1, find_skills | miss -> proposal; hit -> injected skill |
| G3.4 model hierarchy | OpenVault identity (exists) | no silent substitute |
| G3.5 goal on chat turn | G2 seeker/OSR | goal_id on agentic chat; silence litmus |
| G3.6 chat subagents | G3.2, existing depth gate | sibling isolation |

Promote out of parking: owner explicit decision (PARKING_LOT rule), then one slice on `docs/dms/BUILD_PLAN.md` or a G3 packet, then STATUS.

**First builder slice after this PRD is accepted:** G3.0 only.

---

## 9. Citations

- distill: `skill_distill/captures/2026-08-25_cursor_cloud-agent-loop.md`
- distill: `skill_distill/captures/2026-07-25_claude-code_all-lanes.md`
- distill: `skill_distill/captures/2026-07-25_cursor_model-routing-multitask.md`
- distill: `skill_distill/learned/engine_improvements_from_distill.md`
- `docs/subagents_findings/2026-08-23_crew-pointer-watchdog-planes.md`
- `docs/subagents_findings/2026-08-25_crew-ship-gate-estate.md`
