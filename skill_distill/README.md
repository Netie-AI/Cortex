# skill_distill — How to run a session

Goal: make **Netie** as strong at orchestration as Claude Code + Cursor by
distilling their real behavior into captures, then promoting into rules/skills.

## 30-minute session

1. Open `DISTILL.md` and pick unchecked topics.
2. Paste `prompts/ASK_CLAUDE_CODE.md` into Claude Code **in plan/multitask mode**.
3. Paste `prompts/ASK_CURSOR.md` into a Cursor Agent chat (this repo).
4. Save each full reply under `captures/YYYY-MM-DD_<source>_<topic>.md`.
5. Run `python scripts/distill_ingest.py`.
6. Review `learned/INDEX.md` + any new `PARKING_LOT.md` P19 bullets.
7. Promote marked items into `.cursor/` or Cursor user rules.

## Multitask recipe (recommended)

Spin **four** parallel asks (Claude Code multitask OR Cursor Task agents):

| Lane | Prompt focus | Output file suffix |
|------|----------------|--------------------|
| A | Memory + RAG | `_memory` |
| B | Tools / MCP / Skills | `_tools` |
| C | Subagents + one-shot plan | `_agents` |
| D | Cloud deploy + scale | `_cloud` |

Merge with ingest; never edit `DISTILL.md` topic checklist until a capture exists.

## Store rule

Raw model output → `captures/` only.  
Normalized engine facts → `learned/`.  
Deferred → `PARKING_LOT.md` P19 with `distill:` cite.
