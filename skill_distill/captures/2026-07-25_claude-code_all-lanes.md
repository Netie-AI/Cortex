# Capture — Claude Code internals, live paste, all four lanes

```yaml
id: 2026-07-25_claude-code_all-lanes
source: claude-code
date: 2026-07-25
operator: Jian Hong (live ASK_CLAUDE_CODE.md paste into Claude Code v2.1.212, model claude-fable-5, Windows 11, repo D:\Cortex)
prompt_used: skill_distill/prompts/ASK_CLAUDE_CODE.md
distill_trace: skill_distill/DISTILL.md
status: raw
```

**Method.** Plan inline (autonomous session — approval-gated plan mode would block), then four parallel background doc-research subagents (memory | tools | agents | cloud) + direct observation of the live harness, `claude --help` / `mcp` / `plugin` / `agents` help, `~/.claude` on-disk state, live settings files, and session transcript JSONLs.
**Evidence tags:** `observed` = seen in this session/on this machine (v2.1.212). `docs` = official page fetched 2026-07-25 (URL in Sources). `inferred` = reasoned, not re-verified. This capture settles P19 debt "Live Claude Code paste of ASK_CLAUDE_CODE.md".

---

## Raw answer

### 0. Runtime identity (PART 0)

- Runtime: Claude Code CLI v2.1.212; interactive by default, headless via `-p/--print` with `--output-format text|json|stream-json`; also desktop app, web (claude.ai/code), IDE extensions. — evidence: observed (`claude --version`, `--help`) — confidence: high
- Modes observed in CLI: interactive, print/headless, background agent (`--bg`, managed by `claude agents`), plan mode, permission modes (`manual`, `plan`, `acceptEdits`, `dontAsk`, `auto`, `bypassPermissions`), `--safe-mode` (all customizations off), `--bare` (skip hooks, LSP, plugins, attribution, **auto-memory**, CLAUDE.md auto-discovery), `--worktree`, `--remote-control`, `--effort low|medium|high|xhigh|max`. — evidence: observed (`--help`) — confidence: high
- On-disk homes: `~/.claude/{settings.json, projects/, plugins/, sessions/, tasks/, shell-snapshots/, session-env/, telemetry/, backups/, cache/, downloads/, .credentials.json, mcp-needs-auth-cache.json}`; project: `.claude/settings.json`, `.claude/settings.local.json`, `.claude/launch.json`, `.mcp.json`, `.claude/{skills,agents,commands,rules}/`. — evidence: observed (dir listing) — confidence: high

### 1. One-shot plan → task graph → execution → summary (PART 3)

**Exact lifecycle:**

1. **Goal intake → plan.** Plan mode is a permission mode (read-only research; edits blocked). Model-side tools: `EnterPlanMode`, clarification via `AskUserQuestion`, then `ExitPlanMode` presents the plan for approval with options "Yes and use auto mode" / "Yes, manually approve edits" / "Keep planning" / "No"; Shift+Tab cycles modes interactively; headless `--permission-mode plan`. — evidence: observed (tool schemas in live harness) + docs (permission-modes) — confidence: high
2. **Task graph.** Work items tracked via `TaskCreate`/`TaskList`/`TaskUpdate`/`TaskGet`/`TaskStop`/`TaskOutput` tools (surfaced to user; `/tasks`); harness nudges the model to keep the list current via system reminders. — evidence: observed (deferred tool names + live system reminders) + docs (agents.md, med detail) — confidence: high
3. **Execution, four parallelism mechanisms:**
   - *Parallel tool calls*: independent calls batched in one assistant message run concurrently; read-only tools run concurrently, state-modifying tools sequentially. — evidence: observed (harness instruction) + docs (agent-sdk/agent-loop) — confidence: high
   - *Subagents* (`Agent` tool): background by default (docs: v2.1.198+), completion arrives as `<task-notification>`; `run_in_background: false` for sync. — evidence: observed + docs (sub-agents) — confidence: high
   - *Background sessions*: `claude --bg` / `claude agents` fleet view dispatches whole sessions with per-dispatch model/effort/permission-mode/MCP defaults; file isolation via git worktrees; a supervisor process keeps sessions alive after the terminal closes. — evidence: observed (`claude agents --help`) + docs (agent-view) — confidence: high
   - *Workflows* (`Workflow` tool): deterministic JS orchestration script (`agent()`, `parallel()`, `pipeline()`, `phase()`, `log()`), concurrency cap min(16, cores−2), 1000-agent lifetime cap, 4096 items/call, schema-forced structured outputs, token `budget` object. — evidence: observed (tool schema) + docs (workflows) — confidence: high
4. **Final summary.** Harness contract: everything the user needs must be in the final text message of the turn; subagent final reports are not shown to the user — the parent must relay. — evidence: observed (harness instructions) — confidence: high

**What parallel tasks share (filesystem | git | MCP | secrets):**

- Same-session subagents share cwd, filesystem, and git branch by default; `isolation: "worktree"` gives an agent a fresh git worktree auto-removed if unchanged; `isolation: "remote"` runs in a gated cloud environment. — evidence: observed (Agent tool schema) — confidence: high
- Subagents get a fresh context window inheriting system prompt, project CLAUDE.md, and session tools/skills/MCP — not conversation history, not auto-memory. — evidence: docs (agent-sdk/subagents, context-window) — confidence: high
- `claude agents` background sessions isolate files via worktrees; cloud tasks isolate via one VM + one `claude/*` branch per task. — evidence: docs (agent-view, claude-code-on-the-web) — confidence: high
- MCP: inherited from session config; workflow agents reach MCP via ToolSearch; interactively-authenticated (OAuth) servers may be absent in headless/cron runs. — evidence: observed (Workflow schema + live `higgsfield` needs-auth notice) — confidence: high
- Secrets: subagents inherit the session's process credentials/env — there is no per-subagent secret scoping surfaced; scoping levers are sandbox `credentials.files/envVars` deny/mask (mask requires `network.tlsTerminate`), and in cloud a credential-injecting GitHub proxy so tokens never sit in the sandbox env. — evidence: docs (sandboxing, claude-code-on-the-web) + inferred (absence of per-agent scoping in schemas) — confidence: med
- Failure handling: a failed workflow `agent()` resolves to `null` (siblings continue; no cascade cancel); a throwing pipeline stage drops that item's remaining stages; user can skip an agent mid-run. — evidence: observed (Workflow schema) — confidence: high

**"Invoke then understand every step" — where the step log lives:**

- Full session transcript: `~/.claude/projects/<project-slug>/<session-id>.jsonl` — event types observed: `queue-operation` (enqueue/dequeue), `user`/`assistant` messages chained by `parentUuid` with `isSidechain` + `promptId`, and attachment deltas `deferred_tools_delta`, `agent_listing_delta`, `mcp_instructions_delta` (lazy loading is itself logged). — evidence: observed (files on disk) — confidence: high
- Subagent transcripts: `<session>/subagents/agent-<id>.jsonl` + `agent-<id>.meta.json` with `{agentType, description, toolUseId, spawnDepth}`. — evidence: observed — confidence: high
- Workflow journal: `<session>/subagents/workflows/<runId>/journal.jsonl` with content-addressed step keys (`v2:<sha256>`) enabling cached resume, plus per-agent JSONLs and `workflows/<runId>.json`. — evidence: observed — confidence: high
- Docs stance: transcript format internal/unstable; `/export` is the stable export; retention `cleanupPeriodDays` (default 30); `CLAUDE_CODE_SKIP_PROMPT_HISTORY` suppresses writes. — evidence: docs (sessions) — confidence: high
- Headless step stream: `-p --output-format stream-json` (+ `--include-hook-events`, `--include-partial-messages`, `--forward-subagent-text`, `--replay-user-messages`, `--json-schema` for structured final output, `--max-budget-usd` spend cap). — evidence: observed (`--help`) — confidence: high

### 2. Tools / Skills / MCP (PART 2)

**The five-way split:**

| Thing | What it is | Disk home | Loading |
|---|---|---|---|
| Skill | SKILL.md instructions pack (Agent Skills open standard, agentskills.io) | `.claude/skills/`, `~/.claude/skills/`, plugin `skills/` | description always in context; body on invoke; bundled files on demand |
| Slash command | user-typed `/name`; legacy `.claude/commands/*.md`, current = skills | `.claude/commands/`, skills dirs | invoked → content + `$ARGUMENTS`/`$0..$n` |
| Plugin | distribution unit bundling skills, agents, hooks, MCP, LSP servers, monitors | `~/.claude/plugins/`, marketplaces (git) | enable/disable; `claude plugin details` shows projected token cost |
| MCP server | external tool/resource server | `.mcp.json` (project), user scope, `--mcp-config` | tools as `mcp__<server>__<tool>`; schemas deferred by default |
| CLAUDE.md / rules | always-on or path-scoped instructions | CLAUDE.md hierarchy, `.claude/rules/*.md` | startup inject; path rules lazy-load on matching file access |

— evidence: docs (skills, plugins, mcp, memory) + observed (live skill listing uses `plugin:skill` namespacing; built-in CLI commands like `/help` are explicitly not skills) — confidence: high

- Skill frontmatter: `description`, `disable-model-invocation`, `allowed-tools` (CLI-honored, not SDK), `model`, fork/context options; auto-invocation is description-match driven — Claude auto-suggests/invokes a skill when the task matches its description unless `disable-model-invocation` is set; `/name` remains user-invocable. — evidence: docs (skills.md, agent-sdk/skills.md) — confidence: high
- Skills install path: plugin marketplaces (`claude plugin marketplace add <repo>`, `/plugin install`, `plugin@marketplace` ids); `claude plugin init <name>` scaffolds `~/.claude/skills/<name>/` auto-loaded next session as `<name>@skills-dir`; user settings list git marketplaces (`anthropics/claude-plugins-official` observed live). `claude plugin eval` runs graded eval cases against a plugin (with a no-plugin baseline arm). — evidence: observed (`plugin --help`, user settings.json) + docs (plugins) — confidence: high
- `find-skills` / `npx skills`: NOT confirmed by docs sweep this session; treat as unresolved (experiment E5). What is confirmed is the marketplace/init path above. — evidence: inferred — confidence: low
- **Lazy tool loading (the answer is yes, exposed and default):** deferred tools are listed name-only; `ToolSearch` (`select:`/keyword) injects full schemas mid-turn; calling an unfetched tool fails with InputValidationError; env `ENABLE_TOOL_SEARCH` = unset→on, `true`, `auto`, `auto:N` (percent threshold), `false`; deferred MCP names cost ~120 tokens vs full schemas; schema injections are logged in the transcript as `deferred_tools_delta`. — evidence: observed (live harness + transcript) + docs (agent-sdk/tool-search, context-window) — confidence: high
- MCP mechanics: scopes local/project/user; project `.mcp.json` servers sit ⏸ **pending approval** until user approves (`claude mcp reset-project-choices` resets); transports stdio / HTTP / SSE (+ in-process SDK servers); `claude mcp login|logout` for OAuth; `claude mcp serve` runs Claude Code itself as an MCP server; `add-from-claude-desktop` import; headers support `${ENV_VAR}` expansion; server status surfaced as pending/connected/failed/needs-auth/disabled; tool output capped at 25K tokens (`MAX_MCP_OUTPUT_TOKENS`), overflow saved to a file. — evidence: observed (`mcp --help`, live needs-auth cache) + docs (mcp, agent-sdk/mcp) — confidence: high
- **Hooks:** handler kinds = shell command, HTTP endpoint, MCP tool, LLM prompt, agent. Events: `SessionStart`, `SessionEnd`, `UserPromptSubmit`, `PreToolUse`, `PostToolUse`, `PostToolUseFailure`, `Stop`, `SubagentStart`, `SubagentStop`, `PreCompact`, `PostCompact`, `Notification`, `PermissionRequest`, plus more (docs claim 20+). Matchers: exact tool name, `|` alternation, unanchored regex, `mcp__server__tool`. Exit codes: 0 = success, 2 = blocking, other = non-blocking error. JSON output: `continue`, `stopReason`, `decision`, `suppressOutput`, `systemMessage`, `hookSpecificOutput.permissionDecision: allow|deny|ask|defer`, `updatedInput`, `additionalContext`. Cadence: per-session, per-turn, per-tool. — evidence: docs (hooks, agent-sdk/hooks) + observed (harness states hooks may intercept tool calls; `--include-hook-events` flag) — confidence: high
- **Permission tiers (Restrict/Allow/Request → deny/allow/ask):** settings `permissions.{deny,ask,allow}` rule arrays with syntax like `Bash(npm run test:*)`, `Edit(./src/**)`, path prefixes `/`, `~/`, `./`; observed live: project `.claude/settings.local.json` carrying `permissions.allow: ["Bash(git -C /d/Cortex log --oneline -15)", ...]`. Evaluation order: hooks → deny → ask → permission mode → allow → `canUseTool` callback (SDK); deny blocks even in `bypassPermissions` (SDK flow). Modes this build: `manual` (default), `plan`, `acceptEdits`, `dontAsk`, `auto` (classifier-approved actions; loops 3×/20 before re-prompting), `bypassPermissions` (+ `--allow-dangerously-skip-permissions` to make it available without defaulting). Settings precedence: managed → CLI args → local → project → user. — evidence: observed (settings files, `--help`) + docs (permissions, permission-modes, agent-sdk/permissions, settings) — confidence: high
- **Sandbox:** OS-level Bash sandbox — macOS Seatbelt, Linux bubblewrap, WSL2; default read-everything, write cwd+$TMPDIR; network domain allowlist; per-call escape hatch `dangerouslyDisableSandbox` (observed as a live tool parameter) gated by `allowUnsandboxedCommands`; credential protection via `sandbox.credentials.files/envVars` deny or mask (mask substitutes a sentinel, requires `network.tlsTerminate`); managed lockdown (`allowManagedDomainsOnly`, `allowManagedReadPathsOnly`, `failIfUnavailable`). — evidence: docs (sandboxing) + observed (tool param) — confidence: high
- Scaling 10→100→1000 tools: deferral keeps context flat (names only) so tokens no longer break first; the practical limits shift to search quality (model must issue good ToolSearch queries) and auth (interactive OAuth servers unavailable headless). Plugin token costs are inspectable per plugin (`claude plugin details`). — evidence: observed + docs — confidence: med (ranking internals undocumented)
- Mid-plan toolset pivot: ToolSearch injects schemas mid-session (logged as deltas); MCP connect/disconnect invalidates the prompt cache only when tools are NOT deferred; permission rules editable live via settings/`/permissions`. — evidence: observed + docs (prompt-caching) — confidence: high

### 3. Memory / RAG (PART 1)

- **Per-turn prompt assembly (documented budget example):** system prompt (~4200 tok) → auto-memory MEMORY.md excerpt (~680) → environment info (~280) → deferred MCP tool names (~120) → skill descriptions (~450) → `~/.claude/CLAUDE.md` (~320) → project CLAUDE.md (~1800) → conversation history. Observed live equivalents: CLAUDE.md injected as `claudeMd` system-reminder, MEMORY.md index, userEmail, currentDate, gitStatus snapshot, skill listing. — evidence: docs (context-window) + observed — confidence: high
- CLAUDE.md scopes: managed policy → user `~/.claude/CLAUDE.md` → project `./CLAUDE.md` or `./.claude/CLAUDE.md` → `CLAUDE.local.md`; loads broadest→narrowest; subtree CLAUDE.md lazy-loads when files in that subtree are touched; `@import` up to 4 hops; path-scoped `.claude/rules/*.md` load on matching file access and live in message history (compacted away until re-triggered). — evidence: docs (memory) — confidence: high
- The `#` shortcut to append a memory: long-standing feature, not re-verified this sweep. — evidence: inferred — confidence: med
- **Auto-memory:** on by default; per-project at `~/.claude/projects/<slug>/memory/` (observed live with 8 topic files + MEMORY.md); first 200 lines / 25KB of MEMORY.md loads at session start, topic files on demand; toggles: `/memory`, `autoMemoryEnabled`, `CLAUDE_CODE_DISABLE_AUTO_MEMORY=1`, `--bare`. — evidence: docs (memory) + observed — confidence: high
- **What a turn actually uses:** CLAUDE.md layers + auto-memory index + env/git snapshot + skill descriptions + (on-demand) session-transcript search — the desktop exposes `search_session_transcripts` as an MCP tool; there is no automatic chat-RAG injection. — evidence: observed (live tool) + docs — confidence: high
- **Compaction:** auto-compact triggers as context nears the window limit; `/compact` manual; summary preserves key technical detail, drops verbatim tool outputs; harness continues work across the boundary (summary + remaining context re-presented). Survives compaction: project-root CLAUDE.md, unscoped rules, auto-memory (re-injected from disk); lost until re-triggered: path-scoped rules, nested CLAUDE.md. Hooks: `PreCompact` (matcher manual|auto) and `PostCompact`. `/context` shows live token breakdown. — evidence: docs (context-window, hooks-guide) + observed (harness contract) — confidence: high
- **Retrieval stance: agentic search, not embeddings.** Claude Code ships no vector index; it greps/globs/reads. For big monorepos the official recommendation is to expose your own RAG index as MCP tools. (This is exactly Netie's RawKnn/lexical seam.) — evidence: docs (large-codebases) — confidence: med
- **Prompt caching:** layered prefix cache (system → project context → history); invalidated by model switch, effort change, fast-mode toggle, MCP connect/disconnect (if not deferred), plugin enable/disable, denying a built-in tool, `/compact`, upgrade. Editing root CLAUDE.md mid-session does NOT apply until /clear//compact/restart (reads cached copy). Subscription default 1h TTL, API default 5m (`ENABLE_PROMPT_CACHING_1H=1`). `--exclude-dynamic-system-prompt-sections` moves per-machine bits out of the system prompt for cross-user cache reuse. — evidence: docs (prompt-caching) + observed (flag) — confidence: high
- Sessions: `--continue`, `--resume [id|name]`, `-n/--name`, `--fork-session`, `--from-pr <PR>`, `/branch` conversation copies (inherit session permission grants), `--session-id`, `--no-session-persistence`. — evidence: observed (`--help`) + docs (sessions) — confidence: high

### 4. Cloud agents (PART 3.4–3.5)

- **Spin-off paths:** claude.ai/code web UI, desktop dispatch, mobile app (`/mobile` QR pairing), CLI `--cloud` flag, routines (scheduled), GitHub Actions (`@claude`), Agent SDK (self-hosted), Managed Agents (Anthropic-hosted REST). — evidence: docs (web-quickstart, claude-code-on-the-web, routines, github-actions, agent-sdk/overview) — confidence: high
- **Sandbox:** one isolated Anthropic-managed VM per task (fresh clone; ~4 vCPU / 16 GB RAM / 30 GB disk); gVisor + external network proxy; writes confined to working dir + /tmp; non-allowlisted domains 403 at the proxy; GitHub access through a credential-injecting proxy (token never in sandbox env). — evidence: docs (claude-code-on-the-web, sandboxing, agent-sdk/secure-deployment) — confidence: high
- **Permissions in cloud:** auto-accepted — the sandbox replaces interactive prompts. — evidence: docs (web-quickstart) — confidence: high
- **Environment/secrets:** optional bash setup script (cached until changed, ~7-day expiry; snapshots filesystem, not processes); `.env`-format env vars per environment; no local config — CLAUDE.md must be committed to the repo; Zero-Data-Retention orgs blocked from web sessions. — evidence: docs — confidence: high
- **MCP in cloud:** claude.ai connectors (HTTP/SSE routed via Anthropic) + HTTP servers from committed `.mcp.json`; stdio servers unavailable. — evidence: docs (claude-code-on-the-web, routines) — confidence: high
- **Handoff:** `--teleport` (experimental) moves sessions between infra; Remote Control runs long tasks on your own hardware (`--remote-control` observed in CLI); desktop can dispatch to web. — evidence: docs + observed (flags) — confidence: med
- **Routines = scheduled cloud agents:** triggers cron / API / GitHub events; each fire spawns an isolated permissionless (sandboxed) session; API trigger is `POST .../fire` with bearer token and the external text wrapped in an **untrusted-payload block**; daily run cap per account; available on all paid plans (web sessions need premium seats). — evidence: docs (routines, feature-availability) — confidence: high
- **GitHub Actions:** `claude-code-action@v1` on GitHub runners (not Anthropic VMs), `@claude` mentions on issues/PRs, `ANTHROPIC_API_KEY` from repo secrets, can create PRs/fix issues. — evidence: docs (github-actions) — confidence: high
- **Branch/PR flow:** cloud tasks work on `claude/*` prefixed branches; parallel tasks = separate sessions on separate branches, no worktree management needed; PR auto-fix listens for webhooks. — evidence: docs — confidence: high
- **Local vs cloud differences:** local = your filesystem/credentials/interactive prompts/stdio MCP + OS sandbox opt-in; cloud = fresh clone, VM sandbox, auto-accepted permissions, connectors-only MCP, committed-config only, usage billed to subscription; `claude ultrareview` (aka `/code-review ultra`) is a billed cloud-hosted multi-agent review of the current branch/PR. — evidence: docs + observed (`--help`, session guidance) — confidence: high
- Agent SDK deploy: Python `claude-agent-sdk` / TS `@anthropic-ai/claude-agent-sdk` bundle the full harness (built-in tools) into your process/container; headless CLI is the wire format; Managed Agents = Anthropic-hosted persistent sandbox + SSE event stream + file mounts. — evidence: docs (agent-sdk/overview, managed-agents) — confidence: high

### 5. Subagents (PART 4)

- **Definition:** markdown files in `.claude/agents/` (project) or `~/.claude/agents/` (user) or plugins; frontmatter: `name`, `description` (drives auto-delegation), `tools`, `disallowedTools`, `model` (alias/full id/`inherit`), `effort`, `permissionMode`, `skills`, `memory`, `mcpServers`, `maxTurns`, `background`. `/agents` manages them; CLI `--agents '<json>'` defines them ad hoc; SDK `agents` param programmatically. — evidence: docs (sub-agents, agent-sdk/subagents) + observed (`--agents` flag) — confidence: high
- **Invoke:** automatic (description match), explicit ("use the code-reviewer agent"), or model-called `Agent` tool (renamed from `Task` in v2.1.63); background by default since v2.1.198 (observed live: async launch + `<task-notification>` on completion); `run_in_background: false` forces sync. — evidence: docs + observed — confidence: high
- **Return shape:** the parent receives only the subagent's final message as the tool result; full transcript stays isolated (on disk under `subagents/`); the user does NOT see subagent output — parent must relay. In workflows, `agent(prompt, {schema})` forces a StructuredOutput tool call and returns a validated object. — evidence: docs + observed (harness contract, Workflow schema) — confidence: high
- **Continuation:** this build supports continuing a completed subagent with its context intact via `SendMessage` (new `Agent` call = fresh context). — evidence: observed (harness) — confidence: high
- **Nesting:** modeled and env-gated — `CLAUDE_CODE_MAX_SUBAGENT_SPAWN_DEPTH` (default disabled; max 5 layers in v2.1.172–2.1.216); subagent metadata records `spawnDepth` (observed value 1 on disk). — evidence: docs + observed — confidence: high
- **Tool allowlists:** omit `tools` → inherit all (including MCP); list to restrict; `disallowedTools` subtracts (patterns `mcp__server`, `mcp__*` drop whole servers); CLI-level `--tools`, `--allowedTools`, `--disallowedTools`. — evidence: docs + observed (`--help`) — confidence: high
- **Built-ins:** docs guarantee `general-purpose`; this build additionally ships `claude`, `claude-code-guide`, `Explore`, `Plan`, `statusline-setup` as agent types (Explore/Plan exist here as read-only agent types, distinct from plan permission mode). — evidence: observed (live agent listing; docs differ) — confidence: high (for this build)
- **Parallelism:** no documented hard cap for direct Agent-tool spawns; Workflow runs cap at min(16, cores−2) concurrent / 1000 lifetime. — evidence: docs + observed (schema) — confidence: med
- **Safety:** since v2.1.210 subagent final messages are scanned and instruction-shaped patterns neutralized (observed live twice this session: lane reports came back with a harness neutralization banner). Agent teams (shared task list + inter-agent messaging) are experimental behind `CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1`; teammates load skills/MCP from session defaults, not their agent definition. — evidence: docs + observed — confidence: high
- **Cursor `Task` mapping:** Cursor Task types ↔ Claude Code subagent_type; both take prompt + return final text; Claude Code adds frontmatter-declared tool/model/effort/permission policy per agent and worktree isolation — map at concept level (see 2026-07-25 Cursor capture). — evidence: inferred (cross-capture) — confidence: med

### 6. PART 6 decision matrix — Claude Code column updated (live capture)

| Concern | Claude.app | Claude Code | Cursor | Netie should |
|---------|------------|-------------|--------|--------------|
| One-shot multi-step | Chat + Capabilities tools | Plan mode → task list → parallel subagents / background sessions / Workflow scripts; stream-json step log | Agent (+ Plan mode) + Task | Explicit workflow templates → DAG |
| Memory | Chat search + legacy memory + import | CLAUDE.md hierarchy + path rules + auto-memory dir (200-line index) + sessions on disk; agentic search, no embeddings | User/project rules + codebase index | memory routes + F6; no opaque blob |
| MCP / connectors | Connectors + lazy tool mode | `claude mcp` scopes + project approval gate + OAuth login + deferred schemas via ToolSearch (`ENABLE_TOOL_SEARCH`) + `mcp serve` | GetMcpTools / CallMcpTool | `/mcp/*` + find_mcp; P16 client |
| Skills | Customize Skills | SKILL.md standard + plugins/marketplaces + `plugin init/details/eval` (token-costed, gradeable) | `.cursor/skills` | `skills/*.yaml` + discovery |
| Subagents | Limited in chat | Frontmatter-defined agents; background default; final-message contract; env-gated nesting (≤5); output sanitization | Task types | AGENT_TASK + TOOLSETS |
| Governance | Product safety / model switch | 6-step permission pipeline (hooks→deny→ask→mode→allow→callback) + 5 hook handler kinds + OS sandbox + credential mask | Approval cards | ontology + ledger + hooks |
| Cloud scale | — | claude.ai/code VMs (gVisor, 4vCPU/16GB) + routines w/ untrusted-payload API + GH Actions + Managed Agents | Cloud Task VMs | app_package + P17 engine API |
| UI non-devs | Strongest | CLI-first + desktop/web/mobile dispatch | IDE-first | DMS demo + AirGPT skins |

— evidence: observed+docs per sections above — confidence: high (Code column), unchanged prior values elsewhere

### 7. Netie promote map (concepts only — no invented APIs)

**Build now:**

1. **Deferred tool catalog lifecycle** — names-only listing → explicit search/inject step → injection logged as a ledger event (mirror `deferred_tools_delta`). Netie seam: discovery/find_skills + find_mcp, MCP routes. `promote: skill`
2. **Permission pipeline order** — hooks → deny → ask → mode → allow → callback, deny un-bypassable; encode as the policy-gate ordering. Netie seam: policy gate in engine API. `promote: rule`
3. **Untrusted-payload wrapping** for any externally-triggered run (routines `/fire` pattern) — wrap webhook/API text as data, never instructions. Netie seam: routine_scheduler / routine_routes ingestion. `promote: rule`
4. **Subagent contract** — fresh context, inherit-policy-not-history, final-message-only return, output sanitization, spawn-depth cap recorded per agent. Netie seam: AGENT_TASK executor. `promote: subagent`
5. **Content-addressed step journal** — hash(prompt+opts) step keys → resumable DAG runs with cache hits (mirror `v2:<sha>` journal). Netie seam: dag_runner / workflow_runner resume. `promote: skill`
6. **Compaction survive/re-inject semantics** — durable memory re-injects from disk post-compaction; path-scoped context re-triggers; never let compaction eat governance state. Netie seam: memory context provider. `promote: rule`
7. **Token-costed capability manifests** — every pack/plugin reports projected context cost before enable (mirror `plugin details`). Netie seam: pack manifests / app_package. `promote: skill`

**Park (with condition):**

- Agent teams / inter-agent messaging — experimental upstream; park until stable. `promote: parking`
- Managed-Agents-style hosted persistent sandbox parity — park on P17 engine API stability. `promote: parking`
- Sandbox credential masking behind TLS-terminating proxy — park; pairs with OpenVault P17a. `promote: parking`
- Session teleport/migration between local and cloud — experimental upstream; park. `promote: parking`
- Auto-mode (classifier-approved permissions) — needs a trained approval classifier; park. `promote: parking`
- `find-skills` / `npx skills` exact install path — unresolved by docs sweep; park pending experiment E5. `promote: parking`

### 8. Five validation experiments (CLI / Playwright)

1. **Step-log fidelity (CLI):** `claude -p "list repo top-level dirs" --output-format stream-json --include-hook-events` → assert init/assistant/tool_use/result + hook events, then diff against the session JSONL on disk. Validates §1 step-log claims. *(Cortex home: extend `bench/stress.py` harness runner.)*
2. **Lazy-load lifecycle (CLI):** session with a large MCP config; call a deferred tool without ToolSearch → expect InputValidationError; ToolSearch select → success; re-run with `ENABLE_TOOL_SEARCH=false` and compare `/context` token breakdown. Validates §2 deferral + threshold claims.
3. **Parallel isolation (CLI):** two subagents write the same file — default shared-FS conflict vs `isolation: worktree` divergence; verify `git worktree list` during run and auto-cleanup after. Validates §1 sharing model. *(Cortex home: tests/reliability.)*
4. **Nesting gate (CLI):** default env → subagent attempting to spawn fails; `CLAUDE_CODE_MAX_SUBAGENT_SPAWN_DEPTH=3` → inspect `subagents/*.meta.json` spawnDepth values 1..3. Validates §5 nesting claims.
5. **Cloud + routines (Playwright):** drive claude.ai/code on a scratch repo — launch task, assert `claude/*` branch + sandbox egress 403 on a non-allowlisted domain; create a routine, hit `POST /fire` with bearer, assert the fired session shows the text inside an untrusted-payload block; also probe marketplace for a `find-skills` skill (settles E5/parking). *(Cortex home: demo/dms-ui/e2e Playwright harness.)*

---

## Extracted facts

| Fact | Evidence | Confidence | Promote |
|------|----------|------------|---------|
| Deferred tool loading is default-on; ToolSearch injects schemas mid-turn and injections are logged as transcript delta events | observed | high | skill |
| ENABLE_TOOL_SEARCH supports true, auto, auto:N percent threshold, false | docs | high | none |
| Permission evaluation order is hooks then deny then ask then mode then allow then callback; deny holds even in bypassPermissions | docs | high | rule |
| Permission modes in v2.1.212: manual, plan, acceptEdits, dontAsk, auto, bypassPermissions | observed | high | none |
| Hooks come in five handler kinds (command, HTTP, MCP tool, LLM prompt, agent) across 13+ events with allow/deny/ask/defer decisions and updatedInput rewriting | docs | high | rule |
| OS sandbox: Seatbelt/bubblewrap, write cwd+TMPDIR, domain allowlist, credential deny/mask needing tlsTerminate | docs | high | parking |
| Session step log is per-session JSONL plus subagents/agent-id.jsonl with meta (agentType, toolUseId, spawnDepth) plus workflow journal with content-addressed v2 hash keys | observed | high | skill |
| Subagents return only their final message; fresh context inherits system prompt, CLAUDE.md and tools but not history or auto-memory | docs | high | subagent |
| Subagent nesting is env-gated by CLAUDE_CODE_MAX_SUBAGENT_SPAWN_DEPTH, default off, max 5 layers | docs | high | subagent |
| Subagent final messages are sanitized against instruction-shaped injection since v2.1.210 (observed live banner) | observed | high | rule |
| Subagent frontmatter includes tools, disallowedTools, model, effort, permissionMode, skills, memory, mcpServers, maxTurns, background | docs | high | subagent |
| claude agents is a background-session fleet manager with per-dispatch model/effort/permission/MCP defaults, worktree isolation and a supervisor process | observed | high | skill |
| Workflow runs cap at min(16, cores-2) concurrent and 1000 agents lifetime with resumable cached steps | observed | high | skill |
| Auto-memory is default-on at ~/.claude/projects/slug/memory with 200-line/25KB MEMORY.md index loaded per session; --bare and CLAUDE_CODE_DISABLE_AUTO_MEMORY disable | docs | high | rule |
| Compaction re-injects root CLAUDE.md, unscoped rules and auto-memory from disk; path-scoped rules and nested CLAUDE.md are lost until re-triggered; PreCompact and PostCompact hooks fire | docs | high | rule |
| Claude Code ships no embedding index; retrieval is agentic grep/glob and big-repo RAG is delegated to MCP tools you provide | docs | med | rule |
| Prompt cache: subscription 1h TTL vs API 5m; invalidated by model/effort switch, non-deferred MCP connect, plugin toggle, compact; mid-session root CLAUDE.md edits are inert until reload | docs | high | none |
| Cloud tasks run one gVisor VM per task (4vCPU/16GB/30GB), auto-accepted permissions, claude/* branches, connectors-only MCP, setup script cached ~7 days | docs | high | parking |
| Routines fire isolated sessions from cron/API/GitHub events; the /fire API wraps external text in an untrusted-payload block | docs | high | rule |
| Project .mcp.json servers require explicit user approval before connecting (pending state) | observed | high | rule |
| claude plugin details prints a projected token cost per plugin; plugin eval grades plugins against baseline | observed | high | skill |
| find-skills / npx skills install path could not be confirmed from official docs this session | inferred | low | parking |

## Netie implications

- **Build now:** items 1–7 in §7 (deferred catalog lifecycle, permission pipeline order, untrusted-payload wrapping, subagent contract, content-addressed journal, compaction re-inject semantics, token-costed manifests).
- **Park (condition):** agent teams (upstream experimental), hosted persistent sandbox (P17 stable surface), credential masking proxy (OpenVault P17a), teleport (upstream experimental), auto-mode classifier (needs classifier), find-skills path (experiment E5).
- **Tests required:** E1–E5 above; E1/E3 slot into bench/stress + tests/reliability, E5 into demo/dms-ui/e2e Playwright.

## Citations

- distill: skill_distill/captures/2026-07-25_claude-code_all-lanes.md
- prompt: skill_distill/prompts/ASK_CLAUDE_CODE.md (PART numbering from prompts/MASTER_INTERROGATION.md)
- Settles PARKING_LOT P19 debt: "Live Claude Code paste of ASK_CLAUDE_CODE.md"
- Docs fetched 2026-07-25: code.claude.com/docs/en/ — memory, context-window, sessions, prompt-caching, hooks, hooks-guide, skills, commands, plugins, plugins-reference, mcp, permissions, permission-modes, settings, sandboxing, sub-agents, agents, agent-teams, workflows, checkpointing, agent-view, interactive-mode, large-codebases, claude-code-on-the-web, web-quickstart, routines, scheduled-tasks, github-actions, feature-availability, agent-sdk/{overview, subagents, agent-loop, hooks, permissions, skills, slash-commands, mcp, tool-search, secure-deployment}; platform.claude.com/docs/en/managed-agents/overview
- Observed: Claude Code v2.1.212 CLI help (`claude`, `mcp`, `plugin`, `agents`), live harness tool schemas, `~/.claude` + `~/.claude/projects/D--Cortex` on-disk state, D:\Cortex\.claude settings, live session transcript event shapes

## Action YAML

```yaml
action: one_shot_plan
trigger: user (big goal; plan mode via Shift+Tab or EnterPlanMode)
uses: [tools, memory]
inputs: [goal, clarifying answers (AskUserQuestion), read-only research]
outputs: [plan presented via ExitPlanMode, approval choice incl auto mode]
side_effects: [none until approved (read-only mode)]
failure_modes: [plan rejected, blocking on approval in non-interactive sessions]
observability: [session jsonl, /context]
netie_equivalent: CortexOS/execution/run_plan.py + dag_runner.py (concept map)
promote: rule
```

```yaml
action: spawn_subagent
trigger: system (description match) or user (explicit) or model (Agent tool)
uses: [tools, memory, hooks]
inputs: [prompt, subagent_type, model/effort overrides, isolation worktree|remote, run_in_background]
outputs: [final message only (or schema-validated object in workflows); task-notification on completion]
side_effects: [shared FS/branch edits unless worktree; transcript files under subagents/]
failure_modes: [terminal API error -> null result, user skip, depth gate refusal]
observability: [subagents/agent-id.jsonl + meta.json (spawnDepth), SubagentStart/Stop hooks]
netie_equivalent: CortexOS/execution/agent_task.py (concept map)
promote: subagent
```

```yaml
action: background_agent_dispatch
trigger: user (claude --bg / claude agents view)
uses: [tools, hooks]
inputs: [prompt, cwd, per-dispatch model/effort/permission-mode/mcp-config]
outputs: [detached session under supervisor; JSON listing via claude agents --json]
side_effects: [git worktree per session]
failure_modes: [supervisor death, permission stalls in unattended runs]
observability: [claude agents view, session jsonl per dispatched session]
netie_equivalent: CortexOS/execution/routine_scheduler.py + a2a/runtime.py (concept map)
promote: skill
```

```yaml
action: workflow_orchestrate
trigger: user opt-in (explicit ask / ultracode) then model (Workflow tool)
uses: [tools, hooks]
inputs: [JS script with meta.phases, agent()/parallel()/pipeline(), args, token budget]
outputs: [structured return value; per-phase progress; runId]
side_effects: [up to 1000 agents; worktrees if isolation requested]
failure_modes: [agent -> null on error/skip, budget exhaustion throws, script syntax]
observability: [journal.jsonl content-addressed steps, agent-id.jsonl, /workflows]
netie_equivalent: CortexOS/execution/workflow_runner.py + workflow_store.py (concept map)
promote: skill
```

```yaml
action: invoke_skill
trigger: user (/name) or model (description match; disable-model-invocation opts out)
uses: [tools, memory]
inputs: [skill name, args; SKILL.md body + bundled files lazy-loaded]
outputs: [instructions injected into turn, or forked-subagent result]
side_effects: [context growth only on invoke (progressive disclosure)]
failure_modes: [wrong-skill match, allowed-tools not honored by SDK]
observability: [skill listing in system prompt; transcript]
netie_equivalent: CortexOS/discovery/ find_skills + packs (concept map)
promote: skill
```

```yaml
action: tool_search_load
trigger: model (needs a deferred tool) or system (ENABLE_TOOL_SEARCH policy)
uses: [tools]
inputs: [query select:Name or keywords]
outputs: [full schemas injected as functions block; tool callable]
side_effects: [context growth; logged deferred_tools_delta]
failure_modes: [calling before load -> InputValidationError, poor query recall]
observability: [transcript delta events, /context token breakdown]
netie_equivalent: CortexOS/discovery/ + api/discovery_routes.py (concept map)
promote: skill
```

```yaml
action: call_mcp_tool
trigger: model (task needs external capability)
uses: [tools, hooks]
inputs: [mcp__server__tool + args; server config from .mcp.json/user scope]
outputs: [tool result (25K-token cap; overflow to file)]
side_effects: [external service effects; OAuth token use]
failure_modes: [needs-auth (interactive OAuth absent headless), pending-approval project servers, output overflow]
observability: [PreToolUse/PostToolUse hooks, mcp server status states, transcript]
netie_equivalent: CortexOS/api/mcp_routes.py (concept map)
promote: rule
```

```yaml
action: hook_gate_tool
trigger: system (PreToolUse and 12+ other events)
uses: [hooks]
inputs: [event payload + matcher; handler = command|http|mcp_tool|prompt|agent]
outputs: [permissionDecision allow|deny|ask|defer, updatedInput, additionalContext, continue/stopReason]
side_effects: [can rewrite tool input or block turn]
failure_modes: [hook crash treated per exit code, latency on per-tool cadence]
observability: [--include-hook-events stream, debug filter api,hooks]
netie_equivalent: policy gate in CortexOS/api/engine_routes.py (concept map)
promote: rule
```

```yaml
action: search_memory
trigger: system (session start auto-inject) or model (topic file read / transcript search tool)
uses: [memory]
inputs: [MEMORY.md index (200-line/25KB), topic files, CLAUDE.md hierarchy, path rules]
outputs: [system-reminder context blocks]
side_effects: [none (read)]
failure_modes: [stale memories, index bloat past load cap]
observability: [/memory, /context, memory dir on disk]
netie_equivalent: CortexOS/api/memory_routes.py + memory/context_provider.py (concept map)
promote: rule
```

```yaml
action: compact_context
trigger: system (near window limit) or user (/compact)
uses: [memory]
inputs: [conversation history]
outputs: [summary replacing history; durable layers re-injected from disk]
side_effects: [verbatim tool outputs dropped; path-scoped rules lost until re-trigger; cache rebuild]
failure_modes: [losing in-flight nuance, mid-task summarization]
observability: [PreCompact/PostCompact hooks, /context before-after]
netie_equivalent: CortexOS/memory/context_provider.py + semantic_cache.py (concept map)
promote: rule
```

```yaml
action: switch_model
trigger: user (/model, --model, --effort, fast mode)
uses: [tools]
inputs: [model alias or id, effort level, fallback list (--fallback-model)]
outputs: [session on new model]
side_effects: [full prompt-cache invalidation]
failure_modes: [overload without fallback, cache-cost spike]
observability: [cache creation vs read token counts per response]
netie_equivalent: CortexOS/execution/race_router.py + preset_router.py (concept map)
promote: none
```

```yaml
action: deploy_cloud_agent
trigger: user (claude.ai/code, desktop/mobile dispatch, --cloud)
uses: [cloud, tools]
inputs: [repo + branch, env config (setup script, env vars), prompt]
outputs: [VM-sandboxed session, claude/* branch, PR]
side_effects: [auto-accepted permissions inside sandbox, subscription usage]
failure_modes: [egress 403 on non-allowlisted domains, no stdio MCP, ZDR block]
observability: [web session view, PR trail, teleport to CLI (experimental)]
netie_equivalent: CortexOS/execution/app_package.py + P17 engine API (concept map)
promote: parking
```

```yaml
action: routine_fire
trigger: system (cron) or external (POST /fire bearer, GitHub event)
uses: [cloud, hooks]
inputs: [stored prompt + fired payload (wrapped as untrusted)]
outputs: [isolated cloud session run per fire]
side_effects: [autonomous permissionless run inside sandbox, daily cap]
failure_modes: [payload injection attempts (mitigated by wrapping), cap exhaustion]
observability: [routine run history, session transcript]
netie_equivalent: CortexOS/execution/routine_scheduler.py + api/routine_routes.py (concept map)
promote: rule
```

```yaml
action: install_skill
trigger: user (claude plugin marketplace add / install, plugin init)
uses: [tools]
inputs: [marketplace repo, plugin@marketplace id, or scaffold name]
outputs: [enabled plugin (skills/agents/hooks/MCP/LSP/monitors), skills-dir auto-load]
side_effects: [context cost (inspect via plugin details), settings enabledPlugins]
failure_modes: [manifest validation failure, version drift (SHA pinning)]
observability: [plugin details token cost, plugin eval graded results]
netie_equivalent: CortexOS/execution/app_package.py + discovery refs (concept map)
promote: skill
```
