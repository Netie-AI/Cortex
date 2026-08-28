# multi_agent_coordination (from 2026-07-27 Anthropic distill)

**distill:** `skill_distill/captures/2026-07-27_anthropic_multi-agent-coordination.md`  
**sources:** [when/how](https://claude.com/blog/building-multi-agent-systems-when-and-how-to-use-them) + five-pattern post (user paste)

## Engine policy

1. Prefer **single agent** unless context pollution, true parallelism, or specialization justify **3–10×** token cost.
2. Decompose by **context boundaries**, not by role (planner/coder/tester).
3. Default multi-agent pattern: **orchestrator-subagent** (Cortex workflow DAG).
4. Quality-critical + explicit criteria → **generator-verifier** with max_attempts + fallback; never rubber-stamp.
5. Evolve to agent teams / message bus / shared state only when observed friction matches those failure modes.

## Shipped

- `CortexOS/execution/coordination_patterns.py` + engine API recommend/catalog
- `CortexOS/execution/generator_verifier.py`
- Early-victory hardening on `research.verify` / `audit.verify`

## Align with Cortex routing (from pattern-map + when/how lanes)

- Prefer `race_router.eval_predicates` over free-form verifier yes/no when wiring production GV.
- `gen_cfsm` REGENERATE / AUDIT_FAIL is the enterprise cousin of GV `max_attempts` + fallback.
- Discovery `find_subagents` does **not** spawn — only catalogs.
- Explore-lane note that “coordination API unwired / no tests” was **stale**: routes + 13 unit tests shipped same session.

## Parked (P19)

Agent teams, production message bus, shared-state termination, workflow revise fan-back (+ predicate-wired GV).
