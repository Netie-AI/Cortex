# tool_orchestration (seed from Capabilities UI)

**distill:** `skill_distill/sources/claude_capabilities_2026-07-24.md`

## Facts
- Claude exposes **Tool access mode**: lazy (“when needed”) vs eager (“already loaded”).
- Lazy mode: tools not pre-loaded → less context pressure / less compaction.
- Eager mode: tools always in context → more frequent compaction.
- Connector search can surface tools from a directory dynamically.
- Customize splits **Skills / Connectors / Plugins**.

## Netie mapping
| Claude | Netie |
|--------|-------|
| Load tools when needed | Default: discovery `find_skills`/`find_mcp` + ontology allowlist; don't inject full MCP schemas until needed |
| Tools already loaded | Small fixed WEB_TOOLS + discovery schemas in agent_task broker only |
| Skills | `skills/*.yaml` + `CortexOS/discovery` refs |
| Connectors | MCP HTTP catalog + P16 third-party client (parked) |
| Plugins | Packs (`packs/*`) |

## Promote
- rule: prefer lazy tool catalogs
- parking: full connector-search UX (P19)
