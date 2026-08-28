---
id: 2026-07-27_anthropic_multi-agent-when-and-how
source: manual
date: 2026-07-27
operator: cursor-subagent (research lane) + parent merge
prompt_used: parent brief + Anthropic blog paste
distill_trace: skill_distill/DISTILL.md
status: normalized
companion: skill_distill/captures/2026-07-27_anthropic_multi-agent-coordination.md
evidence:
  - agent-tools blog fetch (when-and-how)
  - CortexOS/execution/* skim
  - skill_distill/learned/engine_improvements_from_distill.md
  - PARKING_LOT.md P19
---

## Raw answer

Full integration plan from research lane [Integrate when-to-use blog](789a682e-e67d-4af6-9409-5bab6dcc5777):

- Start single-agent unless context pollution / parallelization / specialization
- Multi-agent ~3–10× tokens; parallel benefit is thoroughness not always speed
- Context-centric decomposition; blackbox verification is a good split
- Early victory: concrete criteria, full suite, negative tests, MUST-run language
- Generator–verifier + pattern gate = build now on existing DAG (no third orchestrator)
- Agent teams / message bus / shared-state multi-writer = park P19
- Align verify loop with `race_router.eval_predicates` (predicate > judge) and `gen_cfsm` TERMINATE/REGENERATE
- Prefer extending `workflow_templates` annotations over new NodeType
- Never promote problem-centric planner/coder/tester role swarms

### Parent merge note (2026-07-27)

Already shipped in same session (supersedes “build now” for catalog/GV helper):
- `coordination_patterns.py` + `GET/POST /api/engine/coordination-patterns*`
- `generator_verifier.py` + unit tests (13 passed)
- Early-victory prompt hardening

Still open from this capture:
- Wire GV into `workflow_runner` / AGENT_TASK with predicates
- Optional template lint for role-split anti-patterns
- Token-multiplier honesty in telemetry

## Extracted facts

| Fact | Evidence | Confidence | Promote |
|------|----------|------------|---------|
| Predicate fail-closed outranks LLM judge (Cortex law) | race_router + blog early-victory | high | rule |
| gen_cFSM REGENERATE aligns with GV max_attempts | gen_cfsm.py | high | parking |
| Role-split presets are Anthropic anti-pattern | blog | high | rule |
| Pattern gate should stay advisory to OSR/preset_router | research lane | med | parking |

## Action YAML

```yaml
- id: multiagent-predicate-outranks-judge
  promote: rule
  action: |
    Generator–verifier pass/fail must prefer race_router.eval_predicates
    (fail-closed) over free-form LLM rubber-stamp.
  distill: skill_distill/captures/2026-07-27_anthropic_multi-agent-when-and-how.md

- id: multiagent-no-role-swarm
  promote: rule
  action: |
    Do not add planner/implementer/tester agent presets for the same feature;
    context-centric fan-out only.
  distill: skill_distill/captures/2026-07-27_anthropic_multi-agent-when-and-how.md

- id: multiagent-gv-wire-predicates
  promote: parking
  action: |
    Wire run_generator_verifier into workflow_runner with AGENT_TASK wrappers
    and predicate eval; journal each attempt.
  condition: after GV helper proven; align with gen_cfsm regenerate
  distill: skill_distill/captures/2026-07-27_anthropic_multi-agent-when-and-how.md
```

## Netie implications

- Build now: done (catalog + GV helper + prompts) — see companion capture
- Park: GV↔workflow wiring with predicates; agent teams; message bus; shared-state termination; role-swarm presets forever refused
- Tests required: predicate fail-closed when wiring production path

## Citations

- distill: skill_distill/captures/2026-07-27_anthropic_multi-agent-when-and-how.md
- distill: skill_distill/captures/2026-07-27_anthropic_multi-agent-coordination.md
- https://claude.com/blog/building-multi-agent-systems-when-and-how-to-use-them
