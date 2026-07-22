# S1 — Token / cost budget for agent runs (Ponytail-aligned)

**Task:** A4 (research only)  
**Date:** 2026-07-22  
**Status:** Findings — no implementation in this packet  
**Scope:** Budget ceilings, hard rails, metering hooks, and abuse cases for the S1 watcher workflow (`detect → draft → approve → publish`).

---

## Verdict (one line)

S1 stays cheap if **detect = T0 / 0 LLM forever**, **draft = template ± one Q2 SQL-layer call**, and **publish never runs without human approve** — wire meters into F1 + (when LLM appears) `CostLedger`, not into the detector.

---

## Per-step token budget table

Ponytail tiers from `CortexOS/ponytail/middleware.py` (`TOKEN_BUDGETS`): **T0=0, T1=512, T2=4096, T3=16000**. S1 maps steps onto those tiers below. “LLM tokens” = billed model prompt+completion; “SQL cost” = lakehouse/ops DB only (not MYR model spend).

| Step | Code path | LLM tokens (budget) | Ponytail tier | MYR model cost | Notes |
|------|-----------|---------------------|---------------|----------------|-------|
| **Detect** | `packs/dms/agents/detectors.py` → `evaluate()` | **0 hard** | **T0** | **RM 0.00** | Pure SQL over lakehouse (`rowcount` / `threshold` / `staleness`). No model, no Ponytail compress, no `answer_engine`. |
| **Draft (template)** | `packs/dms/agents/employee.py` → `_draft_report()` | **0** | T0 | RM 0.00 | Markdown string from agent name + `Detection.detail`. Always runs when fired. |
| **Draft (+ context)** | `_draft_report()` → `CortexOS.dms.answer_engine.answer(context_question)` | **0 default** (L0/L1); **≤512 if future slot-filler**; **never T3 on hot path** | Prefer **T0**; optional **T1** only behind explicit flag | RM 0.00 while L2 off | Context included only when `route == "sql"`. L2 (`DMS_L2_ENABLED`) is flag-off and not wired. Cap: **one** `answer()` call per fired run. |
| **Compliance verdict** | `_compliance_verdict()` | **0** | T0 | RM 0.00 | Deterministic ratio vs bound; sets `requires_human: True`. |
| **Approve** | `approve_run()` / API `POST .../approve` | **0** | T0 | RM 0.00 | Human steward+ only. Writes artifact; no model. |
| **Reject** | `reject_run()` | **0** | T0 | RM 0.00 | Status flip + F1 event. |
| **Publish (today)** | `approve_run()` → `outputs/<approver>/<run_id>/report.md` | **0** | T0 | RM 0.00 | File write only. |
| **Publish (F8 later)** | Future tool call after approve | **0 for tool glue**; any generative export stays **≤T2 (4096)** and ceiling-gated | T0–T2 | Via `CostLedger` when adapters used | F8 must not auto-fire from detect/draft. |

### Recommended numeric ceilings (ops policy)

| Meter | Recommended default | Rationale |
|-------|---------------------|-----------|
| Detector LLM calls / run | **0** (assert in tests) | Spec + `detectors.py` docstring |
| `answer_engine` calls / fired run | **≤ 1** | One `context_question` |
| Draft context token estimate (chars/4) | **≤ 512** (T1) if any LLM slot-filler ever enabled | Ponytail T1 |
| Per-run workflow `cost_ceiling_myr` (when F8/LLM exists) | **≤ 0.10 MYR** default; hard stop via `enforce_ceiling` | Align with eval harness spirit (`avg_cost_myr <= 0.50` is suite-level, not per-agent) |
| Agent runs / agent / hour (scheduler) | Cap TBD in implement (e.g. 12–60) | Stops loop abuse when detector stays fired |

---

## Hard rules

1. **Never LLM in the detector.** `detectors.evaluate` is SQL-only. No imports of routers, adapters, `answer_engine`, or Ponytail inside `packs/dms/agents/detectors.py`. A detector trip is free of model spend by construction.
2. **Never publish without human approve.** `employee.approve_run` is the only path that writes artifacts; status must be `pending_approval`. F5-style `requires_human: True` is non-optional. No autonomous publish when F8 lands.
3. **Draft stays template-first.** Generative Brain templates (future) may enrich copy; they must not re-decide fire/no-fire and must respect Ponytail budgets.
4. **Q2 context is best-effort, not a second agent loop.** Failures in `answer()` must not block the draft (current `try/except` pass). Do not retry with escalating LLM tiers.
5. **L2 freeform SQL stays off for S1 hot path.** `DMS_L2_ENABLED` must not be flipped on for watcher `context_question` without a separate gate + cost ceiling.
6. **Ponytail: delete/compress before spend.** Prefer certified/governed SQL (0 tokens) over cloud. Prefetch once; do not rebuild full warehouse dumps into every draft.
7. **Never skip F1 audit for agent steps.** Even zero-cost steps (`agent.checked`, `agent.drafted`, `agent.published`) must append to the ledger when ops DB is available.
8. **Do not ponytail away security / ledger / adversarial tests** (see `docs/PONYTAIL.md`).

---

## Metering hooks (where to record costs)

Two ledgers exist; S1 must use both intentionally:

### A. F1 audit ledger (governance spine — always on for S1)

| Hook | File | What to record |
|------|------|----------------|
| Step events (already) | `packs/dms/agents/employee.py` → `_audit()` | `agent.checked`, `agent.detected`, `agent.drafted`, `agent.published`, `agent.rejected` |
| Agent lifecycle | `packs/dms/agents/registry.py` | `agent.created` (+ future update/disable) |
| Append/persist | `packs/dms/audit/ledger.py` | Hash-chained entries → `data/dms_ops.db` or `DMS_OPS_DB` / optional `DMS_LEDGER_DSN` |
| Ponytail estimate (when used) | `CortexOS/ponytail/middleware.py` → `ponytail_process` | Event `ponytail.processed` with `tier`, `token_estimate`, `flags`, `truncated` |

**Gap to close in implementation:** draft path that calls `answer_engine` should also emit a cost-ish payload on `agent.drafted` (or a sibling event), e.g. `{layer, route, llm_tokens: 0, answer_calls: 0|1}` — today `agent.drafted` only stores `verdict`.

### B. Runtime cost ledger (MYR / tokens — partial; use when any LLM/adapter runs)

| Hook | File | What to record |
|------|------|----------------|
| Pre-call gate | `CortexOS/execution/executor.py` → `invoke_routed_completion` | `estimate_prompt_tokens` + `ledger.enforce_ceiling` **before** adapter |
| Persist spend | `CortexOS/routing/cost_ledger.py` → `CostLedger.add` | `NodeExecutionRecord`: `prompt_tokens`, `completion_tokens`, `cost_myr`, `ceiling_myr`, `tier`, `model` |
| DAG orchestration | `CortexOS/execution/dag_runner.py` | Same ledger for DAG nodes |
| Token estimate helper | `CortexOS/routing/token_estimate.py` (via executor import) | Prompt projection for ceiling |

**S1 wiring rule:** Detect/draft-today stay off `CostLedger` (zero MYR). When F8 or a draft LLM appears, create/hydrate a `run_id` aligned with `dms_agent_runs.run_id` and call `enforce_ceiling` before every adapter call.

### C. API / registry surfaces (rate & config meters)

| Hook | File | Abuse-relevant field |
|------|------|----------------------|
| Hire agent | `CortexOS/api/agent_routes.py` | `context_question` length / allowlist |
| Run | `POST /dms/agents/{agent_id}/run` | Per-actor / per-agent rate limit (F7 remainder pattern) |
| Registry | `packs/dms/agents/registry.py` | `dms_agents.context_question`, `dms_agent_runs` status timeline |

Architecture honesty: `ARCHITECTURE.md` marks **Cost ledger = Partial**; F1 ledger = shipped. S1 budgets must not pretend MYR metering is complete until executor+Postgres path is hydrated for agent runs.

---

## Abuse cases

| Abuse | How it burns money / load | Mitigation (budget policy) |
|-------|---------------------------|----------------------------|
| **`context_question` spam** | Long or adversarial questions on every fire; future L2/RAG/slot-filler would burn T2/T3 | Max length (e.g. 200 chars); steward-only set; allow only certified/metric-shaped questions; refuse `route != sql` for draft context; **1 call/run** |
| **Loop runs (always-fired detector)** | Bound too low / rowcount always true → every schedule tick drafts + optional Q2 SQL | Cooldown after `pending_approval` until approve/reject; max runs/hour; require status transition before re-draft; detector schedule not continuous LLM |
| **Re-run while pending** | Duplicate drafts / duplicate context queries for same breach | Idempotent “already pending” short-circuit (DBOS resume workstream); no second `answer()` |
| **L2 flag flipped in prod** | Freeform LLM SQL on hot path | Keep `DMS_L2_ENABLED` off for S1; if on, hard `cost_ceiling_myr` + deny in employee draft |
| **F8 publish without approve** | Tool side-effects (email/pptx) without human | Only invoke F8 from `approve_run` after status check; compliance `requires_human` |
| **Ponytail T3 on draft** | 16k context inflate | Force `force_tier="T0"` or `"T1"` for agent context; never route draft through T3 patterns |
| **Audit swallow** | `_audit` `except: pass` hides spend | Metering impl should soft-fail metrics but CI smoke asserts ledger chain for happy path (`test_ledger_chain_complete`) |

---

## Links to truth-ground files

| Topic | Path |
|-------|------|
| S1 feature spec | `docs/dms/BUILD_PLAN_V2_LAKEHOUSE.md` § FEATURE S1 |
| Exec packet A4 | `docs/dms/CURSOR_EXEC_PACKET_2026-07-22.md` |
| Detector (SQL-only) | `packs/dms/agents/detectors.py` |
| Workflow rails | `packs/dms/agents/employee.py` |
| Registry / `context_question` | `packs/dms/agents/registry.py` |
| Agent HTTP API | `CortexOS/api/agent_routes.py` |
| Q2 answer engine | `CortexOS/dms/answer_engine.py` |
| Heuristic synthesize (0 LLM) | `CortexOS/dms/query_service.py` (`synthesize_answer`, `rag_answer`) |
| Ponytail budgets / audit | `CortexOS/ponytail/middleware.py`, `CortexOS/ponytail/__init__.py` |
| Ponytail workflow notes | `docs/PONYTAIL.md` |
| F1 ledger | `packs/dms/audit/ledger.py` |
| MYR cost ledger (partial) | `CortexOS/routing/cost_ledger.py` |
| Pre-call ceiling | `CortexOS/execution/executor.py` |
| Architecture (ledger partial) | `ARCHITECTURE.md` §2 |
| Smoke tests | `tests/dms/test_s1_agents.py` |
| Ontology / F8 tooling note | `docs/ontology/CORTEX_ONTOLOGY_PLAN.md` (DAG + cost ceiling) |

---

## Cursor implementation checklist (budgets / meters)

Use this when leaving research and coding (still Ponytail `full`):

1. **Assert detector purity** — extend `test_detector_pure_sql` (or add static/import guard) so `detectors` never imports LLM/router/answer_engine.
2. **Document + enforce 0 LLM on detect** — budget table above as comment or tiny `S1_BUDGET` constants module only if needed (prefer no new module; extend employee).
3. **Cap `context_question`** — validate length + optional certified-question allowlist on `create_agent` / API.
4. **Meter draft context** — on `agent.drafted`, record `{answer_calls, layer, route, token_estimate: 0|n, llm_tokens: 0}`.
5. **Cooldown / rate limit** — skip re-draft if open `pending_approval` for same agent; rate-limit `POST .../run`.
6. **Wire `CostLedger` only when LLM/F8 adapters appear** — `run_id` = agent run id; `enforce_ceiling` before complete; hydrate on resume (DBOS track).
7. **Keep L2 off for S1** — employee refuses context enrichment if L2 would be required; tests with flag on prove refuse/no spend.
8. **F8 publish gate** — tool invocation exclusively from `approve_run` after status == `pending_approval`; ledger `agent.published` includes tool ids + `cost_myr` if any.
9. **Ponytail for any future generative draft** — `force_tier` ≤ T2, compress to budget, PII redact before prompt (`ponytail_process` / security gate).
10. **Do not “ponytail” away** ledger tests, ceiling tests, or approval-gate tests (`docs/PONYTAIL.md`).
11. **Smoke** — keep `test_approval_gate_blocks_publish`, `test_ledger_chain_complete`; add assert `llm_tokens == 0` on detect+draft under default flags.
12. **Ops defaults** — document env: `DMS_L2_ENABLED=0`, optional `S1_MAX_RUNS_PER_HOUR`, optional `S1_RUN_COST_CEILING_MYR` when F8 lands.

---

## Out of scope (this findings doc)

- Implementing DBOS resume, `@agent` chat, or F8 tools (B1/B2/B4).
- Changing Ponytail middleware behavior.
- Turning on L2 freeform SQL.
- Python/JSX code edits in this research turn.
