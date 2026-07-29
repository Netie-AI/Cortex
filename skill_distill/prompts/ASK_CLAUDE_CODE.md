# ASK — Claude Code (paste into Claude Code)

You are helping distill Claude Code internals so we can build **Netie**, a
governed orchestration engine (Cortex). Work in **plan mode first**, then
**multitask** four lanes if available: memory | tools | agents | cloud.

## Hard rules for your answer
1. Save-ready markdown: we will store this verbatim under `skill_distill/captures/`.
2. Every claim: `evidence: observed|docs|inferred` + `confidence: high|med|low`.
3. End with YAML action blocks from PART 7 of the master bank.
4. Do not invent Netie APIs — map to concepts only unless you read the repo.

## Focus questions (Claude Code)

### One-shot plan + multitask
- Exact steps from user goal → plan → task graph → execution → final summary.
- How parallel tasks share (or don't share) filesystem, git branch, MCP, secrets.
- How to “invoke then understand every step” — where is the step log?

### Tools / Skills / MCP
- Skill vs MCP vs plugin vs slash command in Claude Code.
- `find-skills` / Skills CLI install path; when Claude auto-suggests a skill.
- Tool access: lazy load vs always loaded — does Claude Code expose this?
- Hooks and permission tiers (Restrict/Allow/Request).

### Memory / RAG
- What Claude Code uses per turn: CLAUDE.md, memories, chat search, project files?
- Compaction triggers during long coding sessions.

### Cloud agents
- How to spin off, deploy, scale cloud agents; sandbox; MCP in cloud; secrets.
- Differences vs local Claude Code.

### Subagents
- Definition, invoke, return shape, nesting limits, tool allowlists.

### Output
1. Full answers to the above.
2. Copy PART 6 decision matrix (Claude.app vs Claude Code vs Cursor) for Code column.
3. Paste-ready `promote:` items for Netie parking lot vs build-now.
4. 5 Playwright/CLI experiments to validate your claims.

When done, print a one-line path suggestion:
`skill_distill/captures/YYYY-MM-DD_claude-code_<lane>.md`
