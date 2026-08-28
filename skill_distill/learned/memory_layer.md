# memory_layer (seed)

**distill:** `skill_distill/sources/claude_capabilities_2026-07-24.md`

## Facts
- Chat search (RAG over past chats) is separate from “generate memory from history (Legacy)”.
- Memory is manageable (“view and manage”) and importable from other providers.
- Project memory vs chat memory both mentioned under legacy toggle description.

## Netie mapping
| Claude | Netie |
|--------|-------|
| Search chats | Memory routes + RawKnn / semantic cache (A4) |
| Legacy generate memory | F6 skill capture + future durable user memory store |
| Import from other AI | Adapter behind governance — **park** until P17 consumer ask |
| Manage memory UI | Demo + API list/delete — partial |

## Promote
- parking: cross-provider memory import (P19)
- skill: document when to call memory_search vs skill capture
