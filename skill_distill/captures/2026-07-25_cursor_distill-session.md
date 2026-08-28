```yaml
id: 2026-07-25_cursor_distill-session
source: cursor
date: 2026-07-25
operator: cursor-agent (distill-session)
prompt_used: skill_distill/prompts/ASK_CURSOR.md
distill_trace: skill_distill/DISTILL.md
status: normalized
lanes: [modes_rules, tools_mcp, agents_cloud, playwright]
note: Task subagents failed to spawn (max retries); lanes answered in-parent from first-party Cursor runtime instructions + Cortex repo.
```

## Raw answer

### PART A — Orchestration (modes)

**Agent vs Plan vs Debug vs Ask** (`evidence: observed` from Cursor SwitchMode tool + mode docs, `confidence: high`)

| Mode | When | Write? |
|------|------|--------|
| **Agent** | Clear implementation path; execute | Yes |
| **Plan** | Multiple approaches / architecture trade-offs; design first | Read-only design |
| **Debug** | Runtime failures needing systematic evidence | Investigate |
| **Ask** | Explore/explain without changes | Read-only |

Switch proactively when task type changes (e.g. planning → Agent after design). Mode switches require user consent when using SwitchMode.

**One-shot “do the whole plan” vs Claude Code plan mode** (`evidence: inferred` for Claude Code, `observed` for Cursor, `confidence: med`)

- Cursor: user stays in Agent and the model plans+executes in one thread, optionally spawning `Task` children; Plan mode is an explicit read-only design phase.
- Claude Code: typically plan → user approve → execute task graph / multitask (product docs / common UX). Netie should use explicit plan artifact + DAG compile (already: workflow_templates → dag_runner), not silent mega-loops.

### PART B — Task / subagents

**Task tool** (`evidence: observed` from Cursor Task tool schema, `confidence: high`)

- Types: `generalPurpose`, `explore`, `shell`, `cursor-guide`, `ci-investigator`, `bugbot`, `security-review`, `best-of-n-runner`.
- `run_in_background`, `resume`, `interrupt`, optional `model` slug (restricted allowlist), `environment: local|cloud`.
- Cloud: own branch/VM; review links; uncommitted base branches can fail for cloud.
- Isolation: child does **not** see parent user message unless prompt includes it — parent must pass full context in `prompt`.
- Nested: parent can launch multiple Tasks in parallel in one message.

**Netie mapping**

| Cursor | Netie |
|--------|-------|
| Task explore | workflow research agents + web_tools |
| Task generalPurpose | AGENT_TASK + prompt_library |
| tool allowlist per Task | `annotations["tools"]` / TOOLSETS |
| background Task | routine_scheduler / async (partial) |
| cloud Task | portable app package + P17 — **park** full parity |

Paths: `CortexOS/execution/agent_task.py`, `workflow_templates.py`, `prompt_library.py`, `agent_sdk/`.

### PART C — Tools & MCP

**Built-in vs MCP** (`evidence: observed`, `confidence: high`)

- Built-ins: Shell, Grep, Read, Write, Edit, Task, SwitchMode, browser tools, etc. — schemas always available to the agent runtime.
- MCP: **must** `GetMcpTools` before `CallMcpTool`; servers can be `needsAuth` → `mcp_auth`.
- Approval: some MCP/call paths surface native approval cards (`requestSmartModeApproval`).
- Scaling: many servers inflate schema tokens if all prefetched — prefer server-scoped GetMcpTools / pattern search (`evidence: inferred`, `confidence: med`). Aligns with Claude “Load tools when needed”.

**Netie mapping**

| Cursor | Netie |
|--------|-------|
| GetMcpTools | `/mcp/tools` + discovery catalog |
| CallMcpTool | `/mcp/call` |
| on-demand schema | `find_skills` / `find_mcp` (lazy) |
| third-party MCP client | P16 gated |

Paths: `CortexOS/api/mcp_routes.py`, `CortexOS/discovery/find.py`.

### PART D — Memory / context

(`evidence: observed` for rules/skills files; `inferred` for proprietary memory store, `confidence: med`)

- **User rules** (global): Cursor Settings; applied across projects (e.g. Netie distill discipline rule id `16949854`).
- **Project rules**: `.cursor/rules/*.mdc` (`alwaysApply` / globs).
- **Skills**: `.cursor/skills/*/SKILL.md` — agent-discovered when description matches.
- **AGENTS.md**: role/subagent invoke cards (`.cursor/AGENTS.md`).
- **Codebase**: index + tools (Grep/Read) — not the same as Claude “search chats” memory.
- Compaction: long tool traces get summarized by the product; exact thresholds UNKNOWN (`confidence: low`). Netie should keep tool allowlists small + lazy discovery.

### PART E — Cloud

(`evidence: observed` from Task `environment=cloud` docs, `confidence: high`)

- Cloud agent: separate VM/worktree/branch; review via bc-id links; merge is explicit user choice.
- Local-only branches / unpushed state can break cloud base.
- MCP/secrets in cloud may differ from local — treat as reduced tool surface until verified (`evidence: inferred`, `confidence: med`).

### PART F — Cursor vs Claude Code (blunt)

| Concern | Cursor | Claude Code | Netie should |
|---------|--------|-------------|--------------|
| IDE coupling | Strong | CLI/editor-agnostic | Engine API + optional IDE skin |
| Subagents | Task tool typed | Subagents + multitask | AGENT_TASK + typed toolsets |
| Skills | .cursor/skills + user rules | Skills CLI / find-skills | skills/*.yaml + discovery |
| MCP | GetMcpTools/CallMcpTool | MCP + connectors | mcp_routes + P16 |
| Plan | SwitchMode plan | Plan mode productized | workflow_recognizer + templates |
| Governance | Approval cards | Hooks/permissions | ontology + ledger + hooks (shipped partial) |
| Memory | Rules + index | CLAUDE.md + Capabilities memory | memory routes + F6; don't copy opaque cloud memory |

### PART G — Playwright / reliability (lane D)

(`evidence: observed` in repo, `confidence: high`)

- `tests/reliability/test_playwright_discovery.py` — uvicorn + Playwright request/browser against `/api/discovery/*`.
- `demo/dms-ui/e2e/reliability.spec.js` — UI Find Skills.
- `bench/stress.py --scenario discovery`.
- Cursor browser MCP pattern to mirror: navigate → lock → snapshot → act → unlock; avoid blind wait loops.

---

## Extracted facts

| Fact | Evidence | Confidence | Promote |
|------|----------|------------|---------|
| Cursor modes Agent/Plan/Debug/Ask have distinct write and intent boundaries | observed | high | rule |
| Task children need full context in prompt; they do not inherit parent user message | observed | high | rule |
| Always GetMcpTools before CallMcpTool; auth via mcp_auth when needsAuth | observed | high | skill |
| Prefer lazy/on-demand MCP schema fetch when many servers | inferred | med | rule |
| Netie AGENT_TASK + TOOLSETS is the closest Task/subagent analogue | observed | high | none |
| Cloud agents use separate VM/branch; base must be remote-reachable | observed | high | parking |
| Third-party MCP client remains P16; discovery is recommendation-only | observed | high | parking |
| Playwright reliability suite exists for discovery; keep as gate after distill changes | observed | high | none |
| Compaction thresholds for Cursor tool traces are product-internal UNKNOWN | inferred | low | parking |
| distill-session skill + DISTILL.md trace is the store contract | observed | high | skill |

## Action YAML

```yaml
action: spawn_task
trigger: parent_agent_needs_parallel_or_isolated_work
uses: [tools, subagents]
inputs: [description, prompt, subagent_type, model?, run_in_background?, environment?]
outputs: [agent_id, summary_message]
side_effects: [child_workspace_or_cloud_branch]
failure_modes: [missing_context_in_prompt, cloud_base_branch_missing]
observability: [Task tool result, Review links for cloud]
netie_equivalent: CortexOS/execution/agent_task.py + workflow_templates TOOLSETS
promote: rule
```

```yaml
action: call_mcp
trigger: need_external_or_app_control_tool
uses: [tools, mcp]
inputs: [server, toolName, arguments]
outputs: [tool_result]
side_effects: [may_require_auth_or_approval]
failure_modes: [schema_not_fetched, needsAuth, denied]
observability: [GetMcpTools then CallMcpTool]
netie_equivalent: CortexOS/api/mcp_routes.py
promote: skill
```

```yaml
action: switch_mode
trigger: task_type_change_plan_debug_ask_agent
uses: [orchestration]
inputs: [target_mode_id, explanation]
outputs: [user_consent_then_mode]
side_effects: [tool_availability_changes]
failure_modes: [user_rejects_switch]
observability: [SwitchMode tool]
netie_equivalent: NONE (engine has presets/race_router; not IDE modes)
promote: parking
```

```yaml
action: start_cloud_agent
trigger: environment_cloud_on_Task_or_explicit_cloud_request
uses: [cloud, subagents]
inputs: [prompt, cloud_base_branch?]
outputs: [remote_branch, review_link]
side_effects: [VM_provision, git_branch]
failure_modes: [local_only_branch, secrets_missing]
observability: [bc-id Review / Try Live links]
netie_equivalent: execution/app_package.py + P17 — partial
promote: parking
```

```yaml
action: find_skills
trigger: need_capability_for_goal
uses: [tools, skills, discovery]
inputs: [goal, top_k, evolve?]
outputs: [best_match, matches]
side_effects: [optional_skillopt_seed]
failure_modes: [empty_catalog]
observability: [/api/discovery/find-skills, /mcp/call find_skills]
netie_equivalent: CortexOS/discovery/find.py
promote: none
```

## Netie implications

- Build now:
  - Keep lazy discovery as default tool posture (already).
  - Document Task↔AGENT_TASK mapping in learned/.
  - Enforce distill store via existing user rule + project rule (done).
- Park (condition):
  - Full cloud-agent parity (P17/P19).
  - IDE mode switcher analogue inside Netie UI.
  - Opaque compaction threshold mirroring.
- Tests required:
  - `pytest tests/test_discovery tests/dms/test_discovery_routes.py tests/reliability/test_playwright_discovery.py -q`
  - `python -m bench.stress --scenario discovery --threads 4 --iterations 5`
  - `python scripts/distill_ingest.py`

## Paste-ready artifacts

### User rule (already installed as “Netie skill distill discipline”)
See Cursor user rules — distill → `skill_distill/` → ingest → P19.

### Subagent stub
Already in `.cursor/AGENTS.md` as **distill-session**.

## Citations

- distill: skill_distill/captures/2026-07-25_cursor_distill-session.md
- distill: skill_distill/DISTILL.md
