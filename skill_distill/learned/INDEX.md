# Learned index

Normalized facts from `captures/`. Updated by `scripts/distill_ingest.py`.

_Last ingest: 2026-07-29T05:49:56Z_

| Date | Capture | Facts | Promotions |
|------|---------|-------|------------|
| 2026-07-24 | `skill_distill/captures/2026-07-24_claude-app_capabilities-seed.md` | 5 | parking, rule, skill |
| 2026-07-25 | `skill_distill/captures/2026-07-25_claude-code_all-lanes.md` | 22 | none, parking, rule, skill, subagent |
| 2026-07-25 | `skill_distill/captures/2026-07-25_claude-code_distill-inferred.md` | 6 | none, parking, rule, skill |
| 2026-07-25 | `skill_distill/captures/2026-07-25_cursor_distill-session.md` | 10 | none, parking, rule, skill |
| 2026-07-25 | `skill_distill/captures/2026-07-25_cursor_model-routing-multitask.md` | 10 | parking, rule, skill |
| 2026-07-27 | `skill_distill/captures/2026-07-27_anthropic_multi-agent-coordination.md` | 0 | — |
| 2026-07-27 | `skill_distill/captures/2026-07-27_anthropic_multi-agent-when-and-how.md` | 0 | — |
| 2026-07-27 | `skill_distill/captures/2026-07-27_cursor_rag-authority-planes.md` | 0 | — |
| 2026-07-27 | `skill_distill/captures/2026-07-27_cursor_rag-retrieval-hardening.md` | 0 | — |
| 2026-07-29 | `skill_distill/captures/2026-07-29_cortex-honesty_dms-friday.md` | 0 | — |
| 2026-07-29 | `skill_distill/captures/2026-07-29_dms-spaces_chatgpt-for-excel.md` | 0 | — |
| 2026-07-29 | `skill_distill/captures/2026-07-29_pointer-demo_dms-lake-map.md` | 0 | — |

## Auto-extracted facts

### skill_distill/captures/2026-07-24_claude-app_capabilities-seed.md

- (high/observed → rule) Prefer lazy connector/tool loading to reduce compaction
- (high/observed → skill) Keep Skills, Connectors, Plugins as separate registries
- (med/observed → parking) Chat-search memory ≠ legacy generated memory
- (med/observed → parking) Cross-provider memory import is a first-class Claude feature
- (high/observed → parking) Claude Code settings are separate from chat Capabilities

### skill_distill/captures/2026-07-25_claude-code_all-lanes.md

- (high/observed → skill) Deferred tool loading is default-on; ToolSearch injects schemas mid-turn and injections are logged as transcript delta events
- (high/docs → none) ENABLE_TOOL_SEARCH supports true, auto, auto:N percent threshold, false
- (high/docs → rule) Permission evaluation order is hooks then deny then ask then mode then allow then callback; deny holds even in bypassPermissions
- (high/observed → none) Permission modes in v2.1.212: manual, plan, acceptEdits, dontAsk, auto, bypassPermissions
- (high/docs → rule) Hooks come in five handler kinds (command, HTTP, MCP tool, LLM prompt, agent) across 13+ events with allow/deny/ask/defer decisions and updatedInput rewriting
- (high/docs → parking) OS sandbox: Seatbelt/bubblewrap, write cwd+TMPDIR, domain allowlist, credential deny/mask needing tlsTerminate
- (high/observed → skill) Session step log is per-session JSONL plus subagents/agent-id.jsonl with meta (agentType, toolUseId, spawnDepth) plus workflow journal with content-addressed v2 hash keys
- (high/docs → subagent) Subagents return only their final message; fresh context inherits system prompt, CLAUDE.md and tools but not history or auto-memory
- (high/docs → subagent) Subagent nesting is env-gated by CLAUDE_CODE_MAX_SUBAGENT_SPAWN_DEPTH, default off, max 5 layers
- (high/observed → rule) Subagent final messages are sanitized against instruction-shaped injection since v2.1.210 (observed live banner)
- (high/docs → subagent) Subagent frontmatter includes tools, disallowedTools, model, effort, permissionMode, skills, memory, mcpServers, maxTurns, background
- (high/observed → skill) claude agents is a background-session fleet manager with per-dispatch model/effort/permission/MCP defaults, worktree isolation and a supervisor process
- (high/observed → skill) Workflow runs cap at min(16, cores-2) concurrent and 1000 agents lifetime with resumable cached steps
- (high/docs → rule) Auto-memory is default-on at ~/.claude/projects/slug/memory with 200-line/25KB MEMORY.md index loaded per session; --bare and CLAUDE_CODE_DISABLE_AUTO_MEMORY disable
- (high/docs → rule) Compaction re-injects root CLAUDE.md, unscoped rules and auto-memory from disk; path-scoped rules and nested CLAUDE.md are lost until re-triggered; PreCompact and PostCompact hooks fire
- (med/docs → rule) Claude Code ships no embedding index; retrieval is agentic grep/glob and big-repo RAG is delegated to MCP tools you provide
- (high/docs → none) Prompt cache: subscription 1h TTL vs API 5m; invalidated by model/effort switch, non-deferred MCP connect, plugin toggle, compact; mid-session root CLAUDE.md edits are inert until reload
- (high/docs → parking) Cloud tasks run one gVisor VM per task (4vCPU/16GB/30GB), auto-accepted permissions, claude/* branches, connectors-only MCP, setup script cached ~7 days
- (high/docs → rule) Routines fire isolated sessions from cron/API/GitHub events; the /fire API wraps external text in an untrusted-payload block
- (high/observed → rule) Project .mcp.json servers require explicit user approval before connecting (pending state)
- (high/observed → skill) claude plugin details prints a projected token cost per plugin; plugin eval grades plugins against baseline
- (low/inferred → parking) find-skills / npx skills install path could not be confirmed from official docs this session

### skill_distill/captures/2026-07-25_claude-code_distill-inferred.md

- (high/docs → skill) find-skills + Skills CLI is the discovery package manager for Claude skills
- (high/observed → rule) Lazy tool loading reduces context pressure / compaction
- (med/inferred → parking) Claude Code plan→multitask is the UX Netie should emulate with explicit DAG plans
- (med/docs → none) Hooks Restrict/Allow/Request map to Netie confirm_required + hooks (partial)
- (med/inferred → skill) CLAUDE.md-style project instruction file is distinct from chat memory
- (high/observed → parking) Live Claude Code capture still required for high-confidence multitask/cloud internals

### skill_distill/captures/2026-07-25_cursor_distill-session.md

- (high/observed → rule) Cursor modes Agent/Plan/Debug/Ask have distinct write and intent boundaries
- (high/observed → rule) Task children need full context in prompt; they do not inherit parent user message
- (high/observed → skill) Always GetMcpTools before CallMcpTool; auth via mcp_auth when needsAuth
- (med/inferred → rule) Prefer lazy/on-demand MCP schema fetch when many servers
- (high/observed → none) Netie AGENT_TASK + TOOLSETS is the closest Task/subagent analogue
- (high/observed → parking) Cloud agents use separate VM/branch; base must be remote-reachable
- (high/observed → parking) Third-party MCP client remains P16; discovery is recommendation-only
- (high/observed → none) Playwright reliability suite exists for discovery; keep as gate after distill changes
- (low/inferred → parking) Compaction thresholds for Cursor tool traces are product-internal UNKNOWN
- (high/observed → skill) distill-session skill + DISTILL.md trace is the store contract

### skill_distill/captures/2026-07-25_cursor_model-routing-multitask.md

- (high/observed → rule) Child context isolated; parent must pass full prompt
- (high/observed → rule) Child returns summary only into parent
- (high/docs → skill) Custom agents live in `.cursor/agents/*.md` (+ `.claude/`/`.codex/` compat)
- (high/docs → skill) Frontmatter: name, description, model, readonly, is_background
- (high/observed → rule) Task `model` must be allowlisted; no silent substitute
- (high/observed → parking) Observed allowlist includes composer-2.5, composer-2.5-fast, claude-opus-4-8-thinking-high, cursor-grok-4.5-high, gpt-5.6-sol-medium
- (high/docs → parking) Model fallback when blocked (admin/plan/Max Mode)
- (high/docs → skill) Built-in Explore uses faster model
- (high/observed → rule) MCP lazy: GetMcpTools before CallMcpTool
- (high/observed → rule) Precision = rules+skills+agentic Grep/Read + mode + child brief

