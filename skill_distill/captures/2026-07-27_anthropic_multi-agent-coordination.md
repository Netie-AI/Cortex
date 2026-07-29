---
id: 2026-07-27_anthropic_multi-agent-coordination
source: manual
date: 2026-07-27
operator: cursor-agent
prompt_used: skill_distill/prompts/MASTER_INTERROGATION.md (agents lane + Anthropic blog distill)
distill_trace: skill_distill/DISTILL.md
status: normalized
lanes: [multi_agent_coord, generator_verifier, orchestrator_subagent]
sources:
  - https://claude.com/blog/building-multi-agent-systems-when-and-how-to-use-them
  - user-pasted Anthropic post "Multi-agent coordination patterns: Five approaches" (2026-04-10)
---

## Raw answer

### Blog 1 — When and how to use multi-agent systems (2026-01-23)

Multi-agent = multiple LLM instances with **separate conversation contexts**, coordinated through code. Prefer **orchestrator-subagent** as the starting multi-agent pattern.

**Start with a single agent.** Multi-agent typically costs **3–10× tokens** (duplicated context, coordination messages, handoff summaries). Teams often overbuild and lose fidelity at handoffs.

**Three situations where multi-agent consistently wins:**
1. **Context protection** — subtask produces high volume (>~1k tokens) mostly irrelevant to the main task; return a distilled summary.
2. **Parallelization** — explore a larger search space (research facets). Primary benefit is **thoroughness**, not always wall-clock speed.
3. **Specialization** — tool-set / system-prompt / domain expertise conflicts; signals: 20+ tools, domain confusion, new tools degrade old tasks.

**Outgrowing single-agent signals:** approaching context limits; managing 15–20+ tools (try Tool Search / lazy load first); parallelizable subtasks.

**Context-centric decomposition (critical):** divide by **what context an agent needs**, not by software-dev role (planner/implementer/tester). Role splits create telephone-game overhead.

Good boundaries: independent research paths; clean API-separated components; **blackbox verification**.
Bad boundaries: sequential phases of the same feature; tightly coupled components; work requiring frequent shared state sync.

**Verification subagent:** dedicated agent validates with artifact + **clear success criteria** + tools. Sidesteps telephone game. Still valuable when orchestrator is weaker, verification needs specialized tools, or checkpoints are mandatory.

**Early victory problem:** verifier marks pass after one/two superficial checks. Mitigate with concrete criteria, comprehensive checks, negative tests, explicit “MUST run full suite” instructions.

### Blog 2 — Five coordination patterns (2026-04-10)

| Pattern | Use when | Struggles |
|---------|----------|-----------|
| Generator–verifier | Quality-critical + explicit criteria | Vague criteria; inseparability; oscillation |
| Orchestrator–subagent | Clear decomposition, bounded subtasks | Info bottleneck; sequential without parallel |
| Agent teams | Parallel independent **long-running** workers | Cross-talk needs; shared-resource conflicts; completion detection |
| Message bus | Event-driven, growing ecosystem | Tracing; silent drops; router failure modes |
| Shared state | Collaborative real-time findings; no SPOF | Duplication; **reactive loops**; needs termination |

**Evolve:**
- Short bounded → orchestrator-subagent; sustained multi-step familiarity → agent teams
- Predetermined sequence → orchestrator; emergent events → message bus
- Partitioned no mid-flight share → teams; collaborative discoveries → shared state
- Event pipeline → bus; accumulating knowledge base → shared state

**Default recommendation:** start **orchestrator-subagent**; observe friction; evolve. Hybrids are normal.

### Cortex gap map (code evidence)

| Pattern | Status | Evidence |
|---------|--------|----------|
| Single agent | strong | `architecture_presets=minimal`, AGENT_TASK tool loop |
| Generator–verifier | partial→improved | Workflow verify phases existed (one-shot); **no revise loop** until `generator_verifier.py`; prompts lacked early-victory ban |
| Orchestrator–subagent | strong | `workflow_templates` → `dag_runner` + `subagent_contract` |
| Agent teams | none | Parked P19 |
| Message bus | partial | `a2a/` demo handlers only |
| Shared state | partial | memory/RAG/step_journal; no multi-writer termination protocol |

## Extracted facts

| Fact | Evidence | Confidence | Promote |
|------|----------|------------|---------|
| Prefer single-agent unless context pollution / parallel / specialization | Anthropic blog 1 | high | rule |
| Multi-agent ~3–10× tokens | Anthropic blog 1 | high | rule |
| Context-centric decomposition over role splits | Anthropic blog 1 | high | rule |
| Verifier needs explicit criteria + early-victory mitigations | Anthropic blog 1+2 | high | skill |
| Orchestrator-subagent is default multi-agent start | Anthropic blog 2 | high | rule |
| Agent teams = persistent workers ≠ one-shot subagents | Anthropic blog 2 | high | parking |
| Message bus for emergent event pipelines | Anthropic blog 2 | high | parking |
| Shared state needs first-class termination | Anthropic blog 2 | high | parking |
| Cortex already strong on orchestrator-subagent DAGs | CortexOS/execution/workflow_templates.py | high | none |
| Generator-verifier revise loop was missing | code review 2026-07-27 | high | skill |

## Action YAML

```yaml
- id: coord-pattern-catalog
  promote: skill
  action: |
    Ship CortexOS/execution/coordination_patterns.py + GET/POST
    /api/engine/coordination-patterns[ /recommend ]. Decision surface only.
  distill: skill_distill/captures/2026-07-27_anthropic_multi-agent-coordination.md#cortex-gap-map

- id: generator-verifier-loop
  promote: skill
  action: |
    Ship CortexOS/execution/generator_verifier.py with explicit criteria,
    max_attempts, early-victory reject, fallback recording. Wire tests.
  distill: skill_distill/captures/2026-07-27_anthropic_multi-agent-coordination.md#blog-2

- id: early-victory-prompts
  promote: rule
  action: |
    Harden research.verify / audit.verify prompts with early-victory ban +
    criteria_checked fields.
  distill: skill_distill/captures/2026-07-27_anthropic_multi-agent-coordination.md#early-victory

- id: agent-teams-runtime
  promote: parking
  action: |
    Persistent worker pool + shared task queue + conflict partitioning.
  condition: product need for long-running independent migrations / batches
  distill: skill_distill/captures/2026-07-27_anthropic_multi-agent-coordination.md#blog-2

- id: message-bus-harden
  promote: parking
  action: |
    Evolve a2a beyond demo: topic pub/sub, correlation ids, drop metrics.
  condition: event-driven production pipeline demand
  distill: skill_distill/captures/2026-07-27_anthropic_multi-agent-coordination.md#blog-2

- id: shared-state-termination
  promote: parking
  action: |
    Multi-writer shared store protocol with time budget / convergence /
    adjudicator agent; prevent reactive token loops.
  condition: collaborative research product surface
  distill: skill_distill/captures/2026-07-27_anthropic_multi-agent-coordination.md#blog-2

- id: gv-revise-in-workflows
  promote: parking
  action: |
    Optionally wire generator_verifier into workflow_runner so refuted
    research/audit findings re-enter generate phase (today: verify once).
  condition: after generator_verifier unit path proven in production fires
  distill: skill_distill/captures/2026-07-27_anthropic_multi-agent-coordination.md#cortex-gap-map
```

## Netie implications

- Build now:
  - Pattern catalog + recommend API
  - Generator–verifier loop helper
  - Early-victory prompt hardening
  - Recognizer suppress phrases for forced single-agent
- Park (condition):
  - Agent teams runtime
  - Message-bus production A2A
  - Shared-state termination protocol
  - Full revise fan-back inside workflow templates
- Tests required:
  - `tests/test_execution/test_coordination_patterns.py`

## Citations

- distill: skill_distill/captures/2026-07-27_anthropic_multi-agent-coordination.md
- https://claude.com/blog/building-multi-agent-systems-when-and-how-to-use-them
