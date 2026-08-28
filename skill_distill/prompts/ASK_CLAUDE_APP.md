# ASK — Claude.ai / Cowork / Capabilities UI

Paste into Claude.ai (or Cowork). We observed your **Settings → Capabilities** UI.

## Observed settings (confirm / explain internals)

| Setting | UI state we saw | Explain storage + when it runs |
|---------|-----------------|--------------------------------|
| Search and reference chats | ON | |
| Generate memory from chat history (Legacy) | ON | |
| View and manage memory | Updated ~2 days ago | |
| Import memory from other AI providers | Start import | |
| Tool access mode | **Load tools when needed** | vs Tools already loaded — compaction |
| Connector search | (visible) | how ranking works |
| Customize: Skills / Connectors / Plugins | sidebar | differences |
| Claude Code / Cowork / Claude in Chrome | separate nav | shared vs forked runtimes |

## Extra asks
1. What runs on each user message: memory search? tool search? skill match?
2. How Skills in Customize relate to Claude Code Skills CLI packages.
3. How Connectors relate to MCP.
4. Safety: switch models when message flagged — trigger + effect on tools.
5. What a third-party engine (Netie) can legally/technically mirror vs must reimplement.

## Output format
- Section answers
- YAML action contracts (memory_search, tool_lazy_load, connector_search, skill_invoke)
- Netie mapping + confidence
- Experiments to verify

Store as: `skill_distill/captures/YYYY-MM-DD_claude-app_capabilities.md`
