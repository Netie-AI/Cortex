# Source — Claude Capabilities UI (2026-07-24)

Screenshot: Claude Settings → **Capabilities**.

## Observed

### Memory
- **Search and reference chats** — ON — “Allow Claude to search for relevant details in past chats.”
- **Generate memory from chat history (Legacy)** — ON — remembers context for chats and projects.
- **View and manage memory** — link; “Updated 2 days ago”.
- **Import memory from other AI providers** — “Start import” — bring context from another AI account via a provided prompt.

### General / tools
- **Tool access mode** — controls how connector tools load in new conversations.
  - Selected: **Load tools when needed** — “Chats compact less since tools aren’t pre-loaded.”
  - Alternative: **Tools already loaded** — “Chats compact more often since tools are always there.”
- **Connector search** — let Claude search the connector directory and surface relevant tools (partially visible).
- **Switch models when a message is flagged** — safety-related (partially visible).

### Sidebar surfaces
- Settings: General, Account, Privacy, Billing, Usage, Capabilities
- Product: Claude Code, Cowork, Claude in Chrome
- Desktop app: General, Extensions, Developer
- Customize: **Skills**, **Connectors**, **Plugins**

## Status

These are **UI observations**, not confirmed internals. Confirm via
`prompts/ASK_CLAUDE_APP.md` before promoting to rules.
