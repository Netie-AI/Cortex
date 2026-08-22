---
id: 2026-08-22_cursor_orchestration-outside-editor
source: cursor
date: 2026-08-22
operator: cloud-agent
prompt_used: skill_distill/prompts/ASK_CURSOR.md
distill_trace: skill_distill/DISTILL.md
status: normalized
---

# Cursor is not the loop. Cortex is.

A founder paste argued: Cursor cannot run a non-stop agent graph in the
sidebar; orchestrate outside (LangGraph/CrewAI) and use Cursor only as the
editor. This capture keeps the **true** part and inverts the cargo-cult.

## Raw answer

Observed on this Cloud Agent (Cursor, 2026-08-22):

- Session is discrete. One user message, one agent turn, then the run ends.
  Follow-ups arrive as a queue (`cursor-cloud-get-message-queue`), not as an
  in-process event loop.
- Built-in `Task` subagents can run in parallel, including `run_in_background`,
  but the parent still finishes a turn. There is no "Agent A finishes then
  automatically spins Agent B for hours" without an external state engine.
- MCP tools are discovered lazily (`GetMcpTools` then `CallMcpTool`).
- Cloud agents get their own branch + VM. They do not click the Cursor sidebar.
- This repo already forbids a third orchestrator: G1 DAG, routines, seeker,
  A2A, MCP. `architecture_presets` maps `langgraph` to a marketplace adapter
  behind the Cortex gate — it does not import LangGraph.

User intent (this turn): Cortex must retrieve Cursor messages, instruct Cursor,
open a **new** Cursor chat per **new task**, send **normal chat** to the
chatbot repo, and route workspaces: Cortex / Netie / DMS.

## Extracted facts

| Fact | Evidence | Confidence | Promote |
|------|----------|------------|---------|
| Cursor sidebar/cloud runs are session-based; they are not a background state loop | observed | high | rule |
| Cortex (DAG/routines/seeker/A2A) is the orchestration layer; do not add LangGraph as a second orchestrator | docs | high | rule |
| New task -> new Cursor chat; normal chat -> chatbot workspace | observed | high | rule |
| Workspace roots are env-mapped (Cortex, Netie, DMS, chatbot); D:\ defaults are Windows docs, not hardcoded runtime | inferred | high | rule |
| Native click-the-sidebar / open Cursor GUI from the engine process is not available | observed | high | parking |
| Optional HTTP bridge (CORTEX_CURSOR_BRIDGE_URL) can drive a local Cursor sidecar later | inferred | med | parking |

## Action YAML

```yaml
build_now:
  - CortexOS/connectors workspace catalog + Cursor session port
  - dispatch: kind=task opens a new chat; kind=chat stays on chatbot
  - HTTP /api/connectors without importing packs (C2)
park:
  - Cursor GUI automation / sidebar clicks
  - LangGraph/CrewAI/n8n as the loop
  - Windows D:\ process launcher
tests:
  - new task always new chat_id
  - chat kind never opens a Cursor chat
  - DMS language routes to dms workspace
  - no langgraph import
```

## Netie implications

- Build now: engine-side connector port. Packs register nothing. Cursor is a worker surface.
- Park (condition): a local Windows sidecar that actually talks to Cursor's UI, when a desktop bridge exists.
- Tests required: dispatch + retrieve/instruct on an in-process port.

## Citations

- distill: skill_distill/captures/2026-08-22_cursor_orchestration-outside-editor.md
- distill: skill_distill/captures/2026-07-25_cursor_distill-session.md
- G1: `CortexOS/execution/architecture_presets.py` (langgraph = adapter, not runtime)
