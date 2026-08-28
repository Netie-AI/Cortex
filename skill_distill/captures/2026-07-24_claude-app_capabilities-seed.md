```yaml
id: 2026-07-24_claude-app_capabilities-seed
source: claude-app
date: 2026-07-24
operator: cursor-agent
prompt_used: skill_distill/prompts/ASK_CLAUDE_APP.md
distill_trace: skill_distill/DISTILL.md
status: raw
```

## Raw answer

Seeded from UI screenshot (not a live Claude interrogation). See
`skill_distill/sources/claude_capabilities_2026-07-24.md` for full observation.

Summary: Claude Capabilities exposes dual memory toggles (chat search + legacy
generate), memory management/import, tool access mode with lazy-load selected
(better compaction), connector search, and Customize split into Skills /
Connectors / Plugins. Claude Code / Cowork / Chrome are separate product surfaces.

## Extracted facts

| Fact | Evidence | Confidence | Promote |
|------|----------|------------|---------|
| Prefer lazy connector/tool loading to reduce compaction | observed | high | rule |
| Keep Skills, Connectors, Plugins as separate registries | observed | high | skill |
| Chat-search memory ≠ legacy generated memory | observed | med | parking |
| Cross-provider memory import is a first-class Claude feature | observed | med | parking |
| Claude Code settings are separate from chat Capabilities | observed | high | parking |

## Action YAML

```yaml
action: tool_lazy_load
trigger: new_conversation
uses: [tools, connectors, compaction]
inputs: [connector_directory]
outputs: [tools_on_demand]
side_effects: [less_preloaded_schemas]
failure_modes: [missing_tool_until_search]
observability: [tool_access_mode_setting]
netie_equivalent: CortexOS/discovery/find.py + mcp_routes catalog
promote: rule
```

```yaml
action: memory_chat_search
trigger: user_message
uses: [memory, rag]
inputs: [past_chats]
outputs: [relevant_snippets]
side_effects: [prompt_injection_of_history]
failure_modes: [stale_or_irrelevant_hits]
observability: [memory_settings_toggle]
netie_equivalent: memory routes / RawKnn
promote: parking
```

## Netie implications

- Build now: lazy tool policy in Cursor rule + discovery default
- Park: memory import; full connector-search UX; confirm Claude Code internals via ASK_CLAUDE_CODE
- Tests required: discovery stress + Playwright find_skills (already exist)

## Citations

- distill: skill_distill/captures/2026-07-24_claude-app_capabilities-seed.md
- distill: skill_distill/sources/claude_capabilities_2026-07-24.md
