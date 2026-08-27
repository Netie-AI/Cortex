```yaml
id: 2026-08-22_cursor_chat-to-workflow-constructor
source: cursor
date: 2026-08-22
operator: constructor-writer
prompt_used: skill_distill/prompts/ASK_CURSOR.md
distill_trace: skill_distill/DISTILL.md
status: raw
```

## Raw answer

Constructor chat invoked PRD + Epic + Ticket Runner + one file-bounded research lane (PREFLIGHT PARTIAL). Findings: chats die unless capture+ingest; Constructor has 0 Cortex calls; Palantir AIP stays P1; object kinetics is ontology action along DAG edges, not canvas physics. landing#9 first path is complete (Pages 200 + 4 nodes). Ticket Runner seated none. Do not swarm.

Task types used: `prd-agent`, `epic-agent`, `ticket-runner`, `generalPurpose`. Models: inherit. Background: false (parent waited). Cursor MCP GetMcpTools still required before CallMcpTool.

## Extracted facts

| Fact | Evidence | Confidence | Promote |
|------|----------|------------|---------|
| Chat-to-workflow is DISTILL capture + ingest, not a new product | observed | high | skill |
| Constructor v0 has zero fetch to Cortex ontology or dag_runner | observed | high | none |
| Palantir-as-a-service full parity stays PARKING_LOT P1 | docs | high | parking |
| Object kinetics = registered action_type on DAG edge, not canvas physics | inferred | high | none |
| Ticket Runner seats existing writers in parallel; does not spawn one agent per issue | docs | high | rule |
| PRD does not create tickets; Epic does not close tickets | docs | high | rule |
| landing#9 first path complete at https://netie-ai.github.io/constructor/ | observed | high | none |
| O1-O5+O7 ontology plumbing shipped; O6 and P1 not shipped | docs | high | parking |

## Action YAML

```yaml
action: spawn_prd_epic_ticket_research
when: founder asks chat-to-workflow / ontology connectivity / Palantir-as-a-service
preflight: PARTIAL
reuse:
  - ~/.claude/subagents_findings/2026-08-22_fleet-parallel-not-serial.md
  - ~/.claude/subagents_findings/2026-08-22_constructor-ontology-connectivity-gap.md
  - D:/Cortex/PARKING_LOT.md#P1
steps:
  - prd-agent: slice by irreversibility; no tickets; no code
  - epic-agent: completeness from code + live URL; no close
  - ticket-runner: survey+seat; no execute; no swarm
  - generalPurpose: file-bounded measure; write finding
  - parent: write capture + ~/.claude/workflows + python scripts/distill_ingest.py
do_not:
  - merge landing
  - invent host
  - canvas kinetics
  - Constructor fetch to Cortex this week
  - P1 AIP UI
```

## Netie implications

- Build now: this capture + workflow file + ingest (epic 1). Cortex `constructor_graph.py` only on a Cortex writer after ingest (epic 2).
- Park (condition): P1 Palantir AIP parity until paying client + F1-F7 production-hardened. O6 agent studio.
- Tests required: none for ingest; later `pytest tests/dms/test_constructor_graph.py` on Cortex writer.

## Citations

- distill: skill_distill/captures/2026-08-22_cursor_chat-to-workflow-constructor.md
- finding: ~/.claude/subagents_findings/2026-08-22_constructor-ontology-connectivity-gap.md
- agents: prd 1a287edc-4e3a-4a63-ac08-25284f7e17b1; epic d1b0b73e-929b-41a9-877c-58f91fe78446; ticket-runner 75364a00-01d1-4a8d-b20a-d7dd5712c7f3; research e44d14f2-dbd9-470c-a048-a7d50efebe02
