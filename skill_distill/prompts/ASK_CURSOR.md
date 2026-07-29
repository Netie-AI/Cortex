# ASK — Cursor Agent (paste into Cursor on the Cortex repo)

You are distilling **Cursor** agent architecture into Netie. Read
`skill_distill/DISTILL.md` first. Answer in capture-ready markdown.

## Multitask (if Multitask Mode / Task tool available)

Launch parallel explore/generalPurpose agents for:
1. Cursor Agent modes + rules/skills/AGENTS
2. MCP GetMcpTools / CallMcpTool / auth
3. Cloud agents + worktrees + background agents
4. Browser/Playwright reliability patterns

Merge results into one capture file.

## Focus questions (Cursor)

### Orchestration
- Agent vs Plan vs Debug vs Ask — switching criteria.
- How `Task` subagents work (types, model override, background, resume).
- How one-shot “do the whole plan” differs from Claude Code plan mode.

### Tools
- Built-in tools vs MCP tools; schema discovery; approval cards.
- When tools are injected into context vs loaded on demand.
- Scaling to many MCP servers — known limits.

### Memory / context
- What Cursor remembers across chats; rules vs memories vs codebase index.
- Compaction / summarization behavior under long tool traces.

### Skills & rules
- User rules vs project rules vs `.cursor/skills` vs `AGENTS.md`.
- How to auto-store distill outputs (this repo’s `skill_distill/`).

### Cloud
- Cloud agent lifecycle: branch, VM, review links, merge back.
- Secrets, network, MCP availability in cloud vs local.

### Cursor vs Claude Code
Fill differences that matter for Netie (table). Be blunt.

### Netie mapping
For each major Cursor mechanism, cite existing Cortex paths if any
(`CortexOS/discovery`, `execution/agent_task`, `agent_sdk`, …) or `NONE`.

### Required closing artifacts
1. YAML `action:` blocks for: spawn Task, call MCP, switch mode, start cloud agent.
2. Paste-ready Cursor **user rule** enforcing distill → `skill_distill/` store.
3. Subagent stub for `.cursor/AGENTS.md` named `distill-session`.
4. List of checks/tests to run in this repo after distill ingest.

Save suggestion:
`skill_distill/captures/YYYY-MM-DD_cursor_<lane>.md`
Then run: `python scripts/distill_ingest.py --capture <that file>`
