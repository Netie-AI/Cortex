```yaml
id: 2026-08-28_oss_openmanus-openworker-rakazo
source: manual
date: 2026-08-28
operator: cursor-agent
prompt_used: skill_distill/prompts/MASTER_INTERROGATION.md
distill_trace: skill_distill/DISTILL.md
status: normalized
```

## Raw answer

Legal OSS clones only (MIT / Apache-2.0), gitignored at `skill_distill/sources/oss/`.

Refused this session (not cloned, not ingested): jailbreak prompt packs, leaked proprietary system prompts, reconstructed closed Grok bots. R-0015: attach and distill; do not launch founder desktop apps.

### OpenManus (FoundationAgents/OpenManus, MIT)

- Single agent `Manus.create()` + `agent.run(prompt)` (`main.py`).
- `BaseAgent.run`: while `current_step < max_steps` and state != FINISHED; stuck-loop detector on duplicate memory; explicit `Terminated: Reached max steps`.
- ReAct: `think()` then `act()` (`app/agent/react.py`).
- Tools: bash, python_execute, web_search, browser, str_replace_editor, MCP, sandbox (Daytona). Explicit `terminate` tool with status success|failure (`app/tool/terminate.py`) -- the model claims done; nothing independent grades the answer.
- Same budget-stop Cortex already has. Copy the tool catalog shape and stuck-detector, not the terminate-as-success lie.

### OpenWorker (andrewyng/openworker, MIT)

- Desktop coworker: finished deliverable, not chat. Local Python agent server + native shell.
- **Governed by design:** hard floors (human-only, cannot be auto-approved), earned-autonomy ladder, audit trail with approval provenance.
- Security review: "the fixer is never the only checker" -- deterministic scanners + model, then re-scan. Maps 1:1 onto R-0003 and F20b.
- Auto-approve reviewer is a separate model; repeated denials trip a circuit breaker. Unattended runs never self-approve.
- Do not import OpenWorker's connector catalog into AirGPT (F19 / PRD-002). Invert: Cortex permission pipeline + OpenVault custody.

### Rakazo (elie222/rakazo, Apache-2.0)

- Persistent teammates: memory, routines, history; subagent delegation; sandboxes (Docker/E2B/Daytona).
- Maps to AirGPT F19 reusable agents + Cortex F12 crew. Both are NEEDS-YOU. Distill only: persistence and private computers are product clauses, not LOOP-01.

## Extracted facts

| Fact | Evidence | Confidence | Promote |
|------|----------|------------|---------|
| OpenManus loop is max_steps + model `terminate`, no independent quality gate | `app/agent/base.py` run(); `app/tool/terminate.py` | high | none (anti-pattern) |
| OpenWorker fixer != checker; reviewer is a different model | README "Governed by design"; `coworker/reviewer.py` | high | rule (already R-0003) |
| OpenWorker hard floors cannot be prompt-talked past | README governance tiers | high | parking P16 |
| Rakazo persistence/subagents are a product PRD, not an engine ticket | README Features | high | parking PRD-002 / F12 |
| Cortex `generator_verifier` was unwired from AGENT_TASK | `agent_task.py` broke on first non-tool reply | high | skill (LOOP-01) |

## Action YAML

```yaml
build_now:
  - id: LOOP-01
    change: AGENT_TASK quality_criteria + independent _quality_verify; fail-closed if unbound
    cite: CortexOS/execution/agent_task.py
park:
  - item: AirGPT agent_runtime twin loop
    condition: LOOP-01 lands; D09 consumes Cortex HTTP
  - item: OpenWorker approval floors on write tools
    condition: P16 permission pipeline
  - item: Rakazo persistent teammates
    condition: founder PRD-002 or Cortex F12 YES
tests:
  - tests/test_execution/test_f20b_quality_loop.py
```

## Netie implications

- Build now: quality-terminated AGENT_TASK (F20b). Verifier is never the generator.
- Park: second orchestrator, jailbreak prompts, leaked Claude Code system prompts, reconstructed Grok, AirGPT-owned cron, F19 connectors.
- Tests required: fail-closed unbound verifier; fail-then-pass continue; ceiling stop; early-victory reject.

## Citations

- distill: skill_distill/captures/2026-08-28_oss_openmanus-openworker-rakazo.md
- source: skill_distill/sources/oss/OpenManus (MIT)
- source: skill_distill/sources/oss/openworker (MIT)
- source: skill_distill/sources/oss/rakazo (Apache-2.0)
