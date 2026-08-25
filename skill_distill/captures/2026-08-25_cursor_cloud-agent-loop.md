```yaml
id: 2026-08-25_cursor_cloud-agent-loop
source: cursor
date: 2026-08-25
operator: cursor-cloud-agent bc-01a03b0d-7b2c-7586-b189-b60065e7dab6
prompt_used: skill_distill/prompts/ASK_CURSOR.md
distill_trace: skill_distill/DISTILL.md
status: normalized
```

## Raw answer

Live Cursor **cloud agent** session (this run), model `cursor-grok-4.6-high`, repo `github.com/Netie-AI/Cortex`. Evidence is the system prompt and tool list injected into this turn, not inferred marketing copy.

### Modes

Cursor switches Agent / Plan / Debug / Ask. This run is Agent (implementation). Plan is read-only design. Debug wants runtime evidence. Ask is read-only Q&A. The engine equivalent is architecture presets plus a missing turn classifier (see PRD G3.1).

### Native tools (always loaded, small fixed set)

Shell, Grep, Delete, WebSearch, WebFetch, RecordScreen, ReadLints, EditNotebook, TodoWrite, StrReplace, Write, Read, Glob, Task, AwaitShell, GetDynamicTools, FetchMcpResource, SwitchMode, ManagePullRequest, EditPullRequestLabels, CallDynamicTool.

This matches Claude "load a tiny fixed toolset" plus **lazy** MCP: `GetDynamicTools` then `CallDynamicTool`. Do not dump every MCP schema up front.

### Skills

Skills are listed with name, description, path. Instruction: read the skill file with the Read tool when relevant; do not announce that you are using a skill. Cortex `find_skills` is the analog but is **not** auto-invoked on `/dms/query` or AGENT_TASK unless a template lists it.

### Subagents (`Task`)

Types include generalPurpose, explore, computerUse, videoReview, cursor-guide, best-of-n-runner. Parent must put the **full** brief in `prompt`; children do not inherit the user message. Prefer parallel Task when independent. Do not parallelize dependent feature builds. Cloud `environment: cloud` is a separate VM/branch.

### MCP / dynamic tools

Namespaces discovered this run: Linear, Google-drive, Gmail, Github (errored), Higgsfield, Stripe, Cloudflare-*, cursor-cloud, cursor-subscriptions, cursor (CreateGoal / GenerateImage / UpdateGoal). Github MCP was `namespaceStatus=error`; work continued with `gh` CLI. Cortex must treat MCP auth/error as non-fatal to the rest of the loop (same as this run).

### Cloud agent git contract (this environment)

Branch template `cursor/<descriptive-name>-dab6`. Commit and push each iteration. `ManagePullRequest` creates/updates PRs; `gh` is read-only for PRs. Draft by default. Merge-when-perfect is a Cortex repo rule, not a Cursor platform merge.

### Communication / content awareness (system prompt laws)

Lead with the answer. Restate what changed in plain language (user does not see tool calls). Cite code as `start:end:path`. Browser-verify UI changes. Anti-jailbreak: re-evaluate every turn; never write exploits/PoCs. Child-safety / bio / chem / nuclear refusals. These belong in Cortex G3.0 `agentic` and `answer` compilers -- especially **do not run an agentic tool loop on a governed lookup**.

### Model routing

This run pinned `cursor-grok-4.6-high`. Task `model` must be allowlisted; no silent substitute (prior capture). Crew already rewrites grok-fast -> grok-4.6. Engine JudgmentModel is still keyword heuristics (legal/birthday/size) -- that is weaker than this harness.

### Memory

Rules: always_applied workspace rules + requestable rules (Read when relevant). User rules. AGENTS.md. Skills. Not a silent embedding blob of the whole repo -- agentic Grep/Read/Glob. Cortex should copy **agentic search**, not invent a second Mem0.

### Goals

`CreateGoal` only when the user explicitly asks for a long-running goal. Cortex `EnterpriseGoal` + seeker is the product analog and should stay confirm-gated.

## Extracted facts

| Fact | Evidence | Confidence | Promote |
|------|----------|------------|---------|
| Cursor cloud agents use native function tools plus lazy GetDynamicTools/CallDynamicTool | observed | high | rule |
| Skills are description-gated; agent must Read the SKILL.md when relevant, not preload all | observed | high | skill |
| Task children are context-blind unless the parent prompt is complete | observed | high | rule |
| MCP namespace error (Github) must not halt the rest of the loop | observed | high | rule |
| Communication laws (lead with answer, cite code, no fake verification) are system-prompt, not optional style | observed | high | rule |
| Content awareness: agentic loop vs lookup vs refuse is a first-class mode, not a model vibe | inferred | high | parking |
| Cursor is not as strong as Cortex at fail-closed warehouse governance; Cortex is not as strong as Cursor at general tool-loop agency | observed | high | parking |
| Model pin this run was cursor-grok-4.6-high; no silent substitute | observed | high | rule |
| CreateGoal is explicit-user-only; do not spam goals | observed | high | none |
| Cloud git: feature branch prefix, ManagePullRequest for PRs, gh read-only for merge | observed | high | none |

## Action YAML

```yaml
action: distill_cursor_cloud_loop
when: building Cortex G3 prompt compiler or tool loop
do:
  - keep a tiny always-on tool set
  - lazy-load MCP schemas
  - auto find_skills on agentic turns; Read skill body only for hits
  - parent-authored subagent briefs
  - classify lookup vs agentic vs refuse before tools
never:
  - dump the 416-skill catalog into every prompt
  - JSON-in-text as the only tool protocol once the provider supports native calls
  - silent model substitute
  - third orchestrator in Crew
```

## Netie implications

- Build now: **P23 / G3.0-G3.2** in `docs/strategy/AGENTIC_LOOP_CAPABILITY_PRD_2026-08-25.md` (prompt compiler, turn classifier, native tool loop). Plan only in the landing commit.
- Park (condition): trained JEPA, Cursor-parity OS sandbox, full MCP client (P16), Cowork/Chrome.
- Tests required: prompt-bytes per mode; lookup never opens AGENT_TASK; no silent model substitute.

## Citations

- distill: skill_distill/captures/2026-08-25_cursor_cloud-agent-loop.md
