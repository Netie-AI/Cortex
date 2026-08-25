---
keywords: [prd, agentic, system-prompt, crew, dms, constructor, openvault, cursor, claude]
main_idea: Cortex is a strong governed engine and a weak general ReAct loop. Connecting DMS/AirGPT/Pointer does not yield Cursor-class agency. Close G3.0-G3.6 in the PRD. Crew stays a skin.
cite: distill: skill_distill/captures/2026-08-25_cursor_cloud-agent-loop.md
repo: Cortex
date: 2026-08-25
---

# Agentic loop PRD -- ecosystem + capability gap

## Main idea

Eleven product surfaces. Cortex thinks; OpenVault custodians; everything else is a skin. Live Cursor cloud-agent distill shows native tools, lazy MCP, description-gated skills, isolated Task children. Cortex AGENT_TASK is JSON-in-text with max_steps=4. Crew Manager charter is the strongest live prompt and must not become a second engine.

## Golden rule

> Lookup stays on the answer plane. Agentic turns get a compiled system prompt, find_skills, native tools, and OpenVault routes. No third orchestrator.

## Verify

```bash
python scripts/distill_ingest.py --capture skill_distill/captures/2026-08-25_cursor_cloud-agent-loop.md
```
