# claude_code_vs_cursor

**distill:** `skill_distill/captures/2026-07-25_claude-code_all-lanes.md` (live paste — Claude Code column now high confidence)
**distill:** `skill_distill/captures/2026-07-25_cursor_distill-session.md`
**distill:** `skill_distill/captures/2026-07-25_cursor_model-routing-multitask.md` (Cursor multitask + model routing — live/high)
**distill:** `skill_distill/captures/2026-07-25_claude-code_distill-inferred.md` (superseded by all-lanes)
**distill:** `skill_distill/sources/claude_capabilities_2026-07-24.md`

| Concern | Claude.app | Claude Code | Cursor | Netie should |
|---------|------------|-------------|--------|--------------|
| One-shot multi-step | Chat + Capabilities tools | Plan mode → task list → parallel subagents / background sessions (`claude agents`) / Workflow scripts; stream-json step log | Parallel `Task` in one turn + Plan/Agent modes; parent-authored briefs; summary-only return; resume/interrupt/cloud | Explicit workflow templates → DAG |
| Memory | Chat search + legacy memory + import | CLAUDE.md hierarchy + path rules + auto-memory dir (200-line index) + sessions on disk; agentic search, no embeddings | Rules/skills/AGENTS stack + Grep/Read/Glob + codebase index (agentic, not silent blob RAG) | memory routes + F6; no opaque blob |
| MCP / connectors | Connectors + lazy tool mode | `claude mcp` scopes + project approval gate + OAuth login + deferred schemas via ToolSearch (`ENABLE_TOOL_SEARCH`) + `mcp serve` | Lazy `GetMcpTools` → `CallMcpTool`; schemas not preloaded | `/mcp/*` + find_mcp; P16 client |
| Skills | Customize Skills | SKILL.md standard + plugin marketplaces + `plugin init/details/eval` (token-costed, gradeable) | `.cursor/skills` + Find Skills / `Skill` tool | `skills/*.yaml` + discovery |
| Subagents | Limited in chat | Frontmatter-defined agents; background default; final-message-only return; env-gated nesting (≤5); output sanitization | Task `subagent_type` + agent YAML `model:` + optional Task `model` allowlist; no silent substitute | AGENT_TASK + TOOLSETS |
| Model routing | Product picker / Capabilities | Per-agent `model` / effort / CLI flags | Chat picker → agent YAML → Task allowlist (`composer-2.5` / `-fast` / frontier slugs); Composer for explore, frontier for hard lanes | routing table + pack token cost |
| Governance | Product safety / model switch | 6-step permission pipeline (hooks→deny→ask→mode→allow→callback) + 5 hook handler kinds + OS sandbox + credential mask | Approval cards + modes (ask/agent/plan) | ontology + ledger + hooks |
| Cloud scale | — | claude.ai/code VMs (gVisor, 4vCPU/16GB) + routines w/ untrusted-payload fire API + GH Actions + Managed Agents | Cloud Task (`environment: cloud`) VMs / branches | app_package + P17 engine API |
| UI non-devs | Strongest | CLI-first + desktop/web/mobile dispatch | IDE-first | DMS demo + AirGPT skins |
