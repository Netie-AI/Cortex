```yaml
id: 2026-07-25_claude-code_distill-inferred
source: claude-code
date: 2026-07-25
operator: cursor-agent (distill-session)
prompt_used: skill_distill/prompts/ASK_CLAUDE_CODE.md
distill_trace: skill_distill/DISTILL.md
status: raw
note: >
  Not a live Claude Code session. Distilled from public Skills CLI / find-skills docs,
  SkillOpt README, Claude Capabilities UI seed, and common Claude Code UX.
  Re-run ASK_CLAUDE_CODE.md inside Claude Code to upgrade confidence to high.
```

## Raw answer

### One-shot plan + multitask
(`evidence: docs/inferred`, `confidence: med`)

1. User states large goal.
2. Claude Code enters plan (structured task list).
3. User confirms / auto-continues depending on settings.
4. Tasks execute; multitask may parallelize independent tasks.
5. Step log lives in the session UI/transcript; durable artifacts often as files the agent writes.

**Isolation (typical):** shared workspace unless cloud/worktree isolation; tool allowlists via permissions/hooks. Exact process boundaries: UNKNOWN without live Code session (`confidence: low`).

### Tools / Skills / MCP
(`evidence: docs` for find-skills / Skills CLI; `observed` for Capabilities UI, `confidence: high` for Skills CLI, `med` for Code wiring)

- **Skill**: SKILL.md package; install via `npx skills add`; meta skill `find-skills` discovers others.
- **MCP**: servers provide tools; connectors in Claude.app are the productized cousin.
- **Plugin / slash command**: product packaging layers above skills/MCP (`confidence: med`).
- **Lazy tool load** (Capabilities): “Load tools when needed” reduces preloaded schemas → less compaction (`observed` UI).
- **Hooks / Restrict|Allow|Request**: mirrored conceptually by Netie `agent_sdk/hooks.py` + `confirm_required` (Request tier) — P16 remaining veto semantics.

### Memory / RAG
(`evidence: observed` UI + `inferred` Code, `confidence: med`)

- Chat search vs legacy generate-memory are separate toggles in Capabilities.
- Claude Code additionally leans on `CLAUDE.md` / project instructions / file context.
- Compaction drops bulky tool payloads first (inferred industry pattern, `confidence: low`).

### Cloud agents
(`evidence: inferred`, `confidence: med`)

- Provisioned remote runners with sandbox + optional MCP.
- Scale = more parallel cloud sessions, not one infinite local loop.
- Secrets injected by product; don't assume local env vars.

### Subagents
(`evidence: inferred`, `confidence: med`)

- Defined by prompt + tools + model/effort.
- Return summary to parent; nesting depth limited by product.
- Map to Netie `AgentSpec` + TOOLSETS.

### Experiments to validate (run in Claude Code later)
1. One-shot plan with 4 parallel file edits — observe conflict handling.
2. Install find-skills; ask “Are there any good skills for X?”.
3. Toggle tool access mode; measure compaction frequency (Claude.app).
4. Deny a dangerous tool via permissions — confirm Request tier UX.
5. Cloud agent with MCP — list available tools vs local.

---

## Extracted facts

| Fact | Evidence | Confidence | Promote |
|------|----------|------------|---------|
| find-skills + Skills CLI is the discovery package manager for Claude skills | docs | high | skill |
| Lazy tool loading reduces context pressure / compaction | observed | high | rule |
| Claude Code plan→multitask is the UX Netie should emulate with explicit DAG plans | inferred | med | parking |
| Hooks Restrict/Allow/Request map to Netie confirm_required + hooks (partial) | docs | med | none |
| CLAUDE.md-style project instruction file is distinct from chat memory | inferred | med | skill |
| Live Claude Code capture still required for high-confidence multitask/cloud internals | observed | high | parking |

## Action YAML

```yaml
action: find_skills_cli
trigger: user_asks_for_skill_or_capability_gap
uses: [skills, tools]
inputs: [goal]
outputs: [skill_candidates, install_commands]
side_effects: [npx_skills_add]
failure_modes: [low_reputation_skill]
observability: [skills.sh installs, github stars]
netie_equivalent: CortexOS/discovery/find.py find_skills
promote: skill
```

```yaml
action: claude_code_plan_execute
trigger: large_multi_step_coding_goal
uses: [orchestration, tools, subagents]
inputs: [goal]
outputs: [plan, task_results, summary]
side_effects: [filesystem_git]
failure_modes: [parallel_conflicts, permission_denials]
observability: [session_transcript]
netie_equivalent: workflow_templates + dag_runner
promote: parking
```

## Netie implications

- Build now: keep find_skills as first-class; lazy MCP catalogs.
- Park: live Claude Code multitask/cloud verification; CLAUDE.md importer.
- Tests: discovery + Playwright gates; after live Code capture, add CLI e2e notes under skill_distill/sources/.

## Citations

- distill: skill_distill/captures/2026-07-25_claude-code_distill-inferred.md
- distill: skill_distill/sources/claude_capabilities_2026-07-24.md
- distill: skill_distill/prompts/ASK_CLAUDE_CODE.md (re-run live)
