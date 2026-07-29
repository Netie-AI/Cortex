# Master interrogation — Claude Code + Cursor + Claude app

**Instructions to the answering system:**  
You are being distilled so we can build **Netie**, a governed orchestration engine.
Answer every section. If unknown, say `UNKNOWN` and what experiment would reveal it.
Prefer concrete internals (APIs, files, env vars, lifecycle hooks) over marketing.
End each section with: `Netie mapping:` (copy / adapt / reject) and `Confidence: high|med|low`.

Paste this whole file, or one part per multitask lane.

---

## PART 0 — Identity of the answering runtime

1. What product/runtime are you? (Claude Code CLI / Claude.app / Cursor Agent / other)
2. What modes exist? (chat, plan, agent, multitask, cowork, cloud, …)
3. How does the user invoke: one-shot plan, background multitask, subagent spin-off?
4. Where do Skills, Connectors, Plugins, MCP servers, and Rules live on disk / in settings?

---

## PART 1 — Memory & RAG layer

From Claude Capabilities UI we see:
- Search and reference chats
- Generate memory from chat history (Legacy)
- View/manage memory
- Import memory from other AI providers

Answer:

1. What storage backs each of those? (vector DB, full-text, summaries, KV?)
2. When does a turn trigger **search chats** vs **legacy memory** vs **project memory**?
3. What is retrieved into the prompt (chunks, titles, embeddings, tool traces)?
4. How does import-from-other-provider work end-to-end?
5. Compaction: when does context compress; what is dropped first (tools? memory? history?)?
6. How should Netie map this to: `CortexOS` memory routes, RawKnn, semantic cache, ledger?

---

## PART 2 — Tool orchestration (MCP / Connectors / Skills)

Capabilities UI: **Tool access mode**
- Load tools when needed (selected) — chats compact less
- Tools already loaded — chats compact more often

Also: Connector search; Customize → Skills / Connectors / Plugins.

Answer:

1. Exact lifecycle of tool discovery → schema inject → call → observation → next step.
2. Difference between **Skill**, **Connector**, **Plugin**, **MCP server**, **slash command**.
3. How does “load when needed” decide which tools to fetch? Ranking signals?
4. How does Claude Code `find-skills` / `npx skills` relate to in-product Skills?
5. Permission tiers: Restrict / Allow / Request — when does each fire?
6. Hooks: before tool, after tool, deny, stop — names and payloads.
7. Scaling: 10 vs 100 vs 1000 tools — what breaks first (tokens, latency, auth)?
8. Pivoting: mid-plan toolset change — how is allowlist updated?
9. Netie already has `find_skills` / `find_mcp` / ontology tools — what to mirror?

---

## PART 3 — Claude Code one-shot plan & multitasking

1. Step-by-step: user asks big goal → plan → approve? → execute all tasks.
2. How are tasks parallelized? Isolation boundaries (cwd, git, secrets, tools)?
3. Failure handling: one task fails — cancel siblings or continue?
4. How do “cloud agents” differ from local Claude Code agents?
5. Deploy/scale: how are cloud agents provisioned, billed, sandboxed, given MCP?
6. How do you recommend Netie implement the same with DAG + AGENT_TASK + Task tool?

---

## PART 4 — Subagents

1. How is a subagent defined? (prompt, tools, model, effort)
2. How is it invoked from parent? Sync vs async?
3. What returns to parent (summary only vs full transcript)?
4. Nested subagents — allowed? Depth limit?
5. Cursor `Task` tool vs Claude Code subagents — map 1:1 fields.

---

## PART 5 — Cursor-specific architecture

1. Agent modes: Agent / Plan / Debug / Ask — when to switch.
2. MCP: `GetMcpTools` / `CallMcpTool` / auth flow.
3. Rules: user vs project; skills under `.cursor/skills`; `AGENTS.md`.
4. Multitask Mode / background agents / cloud agents / worktrees.
5. Browser MCP / Playwright — reliability practices.
6. What Cursor does better than Claude Code; vice versa.
7. Choice of code models (Composer vs Claude vs GPT) — routing heuristics.

---

## PART 6 — Claude.app vs Claude Code vs Cursor (decision matrix)

Fill a table:

| Concern | Claude.app | Claude Code | Cursor | Netie should |
|---------|------------|-------------|--------|--------------|
| One-shot multi-step coding | | | | |
| Memory across sessions | | | | |
| MCP / connectors | | | | |
| Skills packaging | | | | |
| Governance / audit | | | | |
| Cloud scale | | | | |
| UI for non-devs | | | | |

---

## PART 7 — Every action distill contract

For **each** major action the product can take (search memory, call MCP, spawn
subagent, compact context, switch model, deploy cloud agent, install skill):

Return YAML:

```yaml
action: <name>
trigger: <user/system>
uses: [<memory|rag|tools|hooks|cloud|…>]
inputs: […]
outputs: […]
side_effects: […]
failure_modes: […]
observability: […]
netie_equivalent: <path or NONE>
promote: rule|skill|subagent|parking|none
```

---

## PART 8 — Experiments we must run

List 10 concrete tests (Playwright, CLI, stress) to verify claims above.
Mark which already exist in Cortex (`tests/reliability`, `bench/stress`).

---

## Closing

1. Top 10 facts Netie must implement in the next 30 days.
2. Top 10 facts to park (`PARKING_LOT` conditions).
3. Exact paste-ready Cursor **user rule** (≤2000 chars) encoding distill discipline.
4. Exact paste-ready **SKILL.md** for a `distill-session` skill.
