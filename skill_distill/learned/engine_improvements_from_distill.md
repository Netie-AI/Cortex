# engine_improvements_from_distill (2026-07-25)

**distill:** `skill_distill/captures/2026-07-25_claude-code_all-lanes.md`  
**distill:** `skill_distill/captures/2026-07-25_cursor_model-routing-multitask.md`  
**distill:** `skill_distill/captures/2026-07-27_anthropic_multi-agent-coordination.md`  
**transcript:** [Cursor multitask distill](b42b75c6-d6b0-4da8-afb0-2c3576574936)

## Shipped into Cortex/Netie engine

| Distill "build now" | Implementation | On the execution path? |
|---------------------|----------------|------------------------|
| Deferred tool catalog + inject log | `CortexOS/execution/deferred_tools.py` + `/api/discovery/tools/{deferred,search,inject}` | yes — routes registered in `api/app.py` |
| Untrusted-payload wrap for external fire | `CortexOS/execution/untrusted_payload.py` + `POST /api/routines/{id}/fire` | yes — `routine_scheduler.run_once(prompt_override=…)` |
| Subagent final-message sanitize + depth gate | `CortexOS/execution/subagent_contract.py` wired in `agent_task.py` | yes — sanitize on return, `_gate_spawn_depth` on entry |
| Content-addressed step journal | `CortexOS/execution/step_journal.py` (`v2:<sha256>`) | yes — `run_dag` records; `workflow_runner.resume` + `POST /api/workflows/resume` replay |
| Permission pipeline order documented | `CortexOS/agent_sdk/hooks.py` → `PERMISSION_PIPELINE` | documentation only (veto hooks still P16) |
| Anthropic coordination pattern catalog | `CortexOS/execution/coordination_patterns.py` + `GET/POST /api/engine/coordination-patterns*` | yes — decision surface; no third orchestrator |
| Generator–verifier revise loop | `CortexOS/execution/generator_verifier.py` (criteria, max_attempts, early-victory reject, fallback) | helper ready; workflow fan-back still parked |
| Early-victory verifier prompts | `prompt_library` `research.verify` / `audit.verify` | yes — criteria_checked + ban text |

## Wiring completed 2026-07-25 (second pass)

The five modules existed and were unit-tested, but two were not actually reachable
from the engine. Both are now on the path, with integration tests that fail if the
wiring is removed:

- **Depth gate.** `assert_spawn_allowed` had zero call sites. Now enforced in
  `run_agent_task` via `_gate_spawn_depth`, before any model call. Semantics mirror
  Claude Code: a top-level workflow subagent is depth 1 and always runs (the
  orchestrator spawning it is not itself an agent); only a spawn from *inside*
  another agent is gated by `CORTEX_MAX_SUBAGENT_SPAWN_DEPTH` (default off, max 5).
  Sibling fan-out reads the parent's depth, never each other's, so a phase of N
  parallel agents is untouched — that invariant is a test.
- **Step journal.** `step_journal` had zero call sites. `run_dag` now keys each node
  on `(prompt template, node id, type, inputs, label, phase, item)`, records the
  result, and replays it when resuming. `workflow_runner.resume(run_id)` and
  `POST /api/workflows/resume` make replay reachable; a run that died in its last
  phase no longer pays for the phases that already succeeded.

### Defect found by wiring it (worth keeping in mind)

The first cut replayed on *every* run whenever the run_id matched. That is wrong:
a run_id is unique only by convention, and the repo's own DAG tests reuse fixed ids
(`run_judge`, `run_cap`). Because the journal persists to disk, the second suite run
in a row replayed the first run's results — the router was never called and the
ledger stayed empty, so two unrelated tests failed. Corrected to the actual Claude
Code semantic: **the journal always records, replay happens only on explicit
resume** (`run_dag(..., resume=True)`). Regression tests now cover record-without-
replay, replay-on-resume, run_id scoping, `CORTEX_STEP_JOURNAL=0`, and a journal
that throws (a broken journal must never fail a run).

## Verification

- `tests/test_execution/test_distill_engine_improvements.py` — 13 tests (modules + wiring)
- `tests/dms/test_distill_engine_api.py` — 5 tests (deferred tools, fire wrap, resume route)
- Full suite: **492 passed, 9 skipped**, and green on two consecutive runs (the
  cross-run pollution above only showed on the second).
- `tests/test_fabrication/test_skill_registry.py` de-hardcoded from 6 skill cards —
  the distill promotion added `find_skills.yaml` and `distill_session.yaml`, which
  broke a count assertion; it now asserts the contract against the directory.
- `.gitignore`: `data/engine/step_journal.db` is a runtime cache, not a source file.

## Still parked (P19)

- Agent teams / nested depth product policy (upstream experimental) — reinforced by Anthropic agent-teams pattern
- Hosted cloud VM parity (P17)
- Credential masking proxy (OpenVault P17a)
- Auto permission classifier mode
- find-skills CLI path confirmation (E5)
- Journal retention: the DB grows one row per node per run forever; no pruning yet.
  **Condition:** first long-lived deployment, or when the file gets inconvenient.
- Message-bus production A2A (topics, correlation, drop metrics)
- Shared-state multi-writer termination protocol
- `generator_verifier` revise fan-back inside `workflow_runner` (verify-once templates remain)
