# EPIC-GOVERN-HARDEN: governed execution hardening

Branch base: claude/cortex-scale-agi-state-fkg2b6 @ 11d6eab. Epic #240; tickets GH-01 #241, GH-02 #242, GH-03 #243, GH-04 #244. Planner lane was read-only. Every claim below cites file:line and is marked VERIFIED (read in this run or by a discovery lane with line cites) or ASSUMED.

## Problem

Cortex sells four governance promises: sensitive data does not reach a model, regulated (legal or financial) requests get a capable tier, a workflow never spends past its ceiling, and the audit ledger is a tamper-evident single chain. The code has one verified hole in each of them.

1. **PII leak on the DAG LLM path.** `redact_for_prompt` exists (packs/dms/security/pii.py:65) but `invoke_routed_completion` builds `prompt_blob` and calls `routed.adapter.complete(final_req)` with no redaction (CortexOS/execution/executor.py:89-110, VERIFIED). That function is the single call path for `llm_judged` (CortexOS/execution/dag_runner.py:170) and `agent_task` (CortexOS/execution/agent_task.py:286) nodes (VERIFIED). A DAG whose context contains an NRIC or email sends it raw.
2. **Legal/financial floor bypass.** When a decision backend is set (`CORTEX_KEV_URL`, CortexOS/routing/judgment_model.py:61-68) and does not abstain, `decide()` returns `Tier(answer.choice)` directly (judgment_model.py:91-95, VERIFIED). The deterministic legal-term rule (judgment_model.py:117-118) and the birthday rule (:114-115) only live in `rules_decide`, so a request mentioning "nric" or "loan" can be served at T0 or T1.
3. **Parallel fan-out overspend.** `run_dag` gates each node in a layer independently against `total_spent + own projection` (dag_runner.py:672-674 into `_gate_cost` :531-542, VERIFIED), then runs the layer through `asyncio.gather` (dag_runner.py:681, VERIFIED). The per-call check in `invoke_routed_completion` (executor.py:99) reads a total that siblings have not yet added to, because `ledger.add` runs after the adapter call (executor.py:129, VERIFIED). N siblings each projected just under the remaining budget all pass (runtime overspend is ASSUMED; it follows directly from the code).
4. **Ledger concurrency proof is weaker than it looks.** The SQLite path holds a module `threading.Lock` (packs/dms/audit/ledger.py:18,195) plus `BEGIN IMMEDIATE` (:199), but the only concurrency test is in-process threads (tests/dms/test_f1_ledger.py:82), which the thread lock alone satisfies. `_connect` sets no busy timeout (ledger.py:98-104, VERIFIED; sqlite default 5s). The Postgres concurrency and append-only trigger tests (test_f1_ledger.py:138,160) exist but the only Postgres CI job runs just the RLS deny test (.github/workflows/rls.yml:45-46, VERIFIED), so they never run in CI.

## Buyer-visible outcome

- A DAG run over customer data produces model calls whose prompt text contains `[REDACTED:nric]` / `[REDACTED:email]` placeholders instead of the raw values, even if no pack is loaded (engine-side default redactor, fail closed).
- A legal or financial request routed through a kev backend reports a tier of at least T2 in the run output and cost ledger row, with the override named in the routing reason.
- A parallel DAG with a workflow ceiling either stays under the ceiling or halts with `WorkflowCostCeilingExceeded` before the overspending layer starts. The run result shows no partial spend beyond the ceiling.
- The audit ledger's `verify()` result is proven `ok` with a gap-free seq under multi-process SQLite writes and under the 20-way Postgres test, in CI, on every PR.

## Non-goals

- No cortex-contract field change, no `contract/` regeneration, no OpenAPI drift.
- No change to `CortexOS/execution/manifest.py` refusals, `tests/contract/**`, `tests/invariants/**`, `.importlinter`, or `CortexOS/crew/ui/**`.
- No new `packs.*` import from `CortexOS/**`; no additions to `_C2_ALLOWLIST`.
- No kev calibration, threshold tuning, or accuracy claim. The KEV learning loop (decision log, labels, shadow, report) is a follow-up epic; nothing here asserts a calibrated number.
- No ledger signing with `CORTEX_LEDGER_SIGNING_KEY` (key custody touches OpenVault; follow-up).
- No encryption-at-rest of chat bodies (F7-ENC-MSG) in this epic.

## Tickets (parallel, pairwise-disjoint write targets, no dependencies)

| ID | Title | Write targets | Fail-closed rule |
|---|---|---|---|
| GH-01 PII-DAG | Redact prompt and system text at the routed-completion choke-point | CortexOS/execution/executor.py, NEW CortexOS/security/redact_port.py, NEW tests/test_execution/test_dag_pii_redaction.py | Engine default redactor always on; a registered pack redactor that raises aborts the call with status=error, never sends unredacted text |
| GH-02 RULES-FLOOR | Deterministic tier floors apply after any backend choice | CortexOS/routing/judgment_model.py, NEW tests/test_execution/test_judgment_rules_floor.py | Floors only raise tiers; a backend can never lower a regulated request below T2 |
| GH-03 PAR-CEIL | Cumulative cost gate for parallel batches | CortexOS/execution/dag_runner.py, NEW tests/test_execution/test_parallel_cost_ceiling.py | A batch whose summed projection breaches the ceiling is refused before any sibling spends |
| GH-04 LEDGER-PROOF | Multi-process SQLite proof, busy timeout, Postgres ledger tests in CI | packs/dms/audit/ledger.py, .github/workflows/rls.yml, NEW tests/dms/test_f1_ledger_multiprocess.py | CI step fails the job if the Postgres ledger tests are skipped |

File overlap check: executor.py (GH-01 only), judgment_model.py (GH-02 only), dag_runner.py (GH-03 only), ledger.py and rls.yml (GH-04 only). GH-03 deliberately does not touch cost_ledger.py or executor.py; the cumulative gate lives in run_dag.

## Risks

- **GH-01 over-redaction.** The phone regex (pii.py:21-23) is broad and may redact numeric content (quantities, SKUs) in prompts. Mitigation: the engine default redactor covers NRIC, email and card only; the DMS pack may register the fuller redactor through the port. Tests assert both redaction of PII and survival of a plain number like `qty 12`.
- **GH-01 boundary.** The redactor must not be reached by importing `packs.*` from executor.py. The port pattern mirrors CortexOS/audit/ledger_registry.py. Registration from packs/dms is out of scope for this epic (keeps write targets disjoint); the engine default makes the path safe without it.
- **GH-02 cost.** Floors raise some kev choices to T2, increasing spend for legal/financial traffic. This is the documented plan rule (Phase 9) and only applies when a backend is set; rules-only routing is unchanged.
- **GH-03 stricter refusals.** A parallel layer that previously completed under-ceiling by luck of ordering can now be refused up front. That is intended fail-closed behaviour; sequential execution is unchanged.
- **GH-04 CI time.** Adds a few seconds to the RLS Proof job; the multi-process test must use a tmp path and spawn-safe workers so it runs on Linux and Windows.

## Verified vs assumed

VERIFIED (read in this run at 11d6eab):
- executor.py:53 route, :89-94 prompt_blob built from raw system and prompt, :99 enforce_ceiling, :110 adapter.complete, :129 ledger.add after the call.
- judgment_model.py:61-68 from_env backend, :70-95 decide returns backend tier, :107-129 rules cascade with legal floor at :117-118 and birthday at :114-115.
- dag_runner.py:531-542 _gate_cost, :672-674 per-node gate, :675-684 parallel gather.
- cost_ledger.py:54-64 total_cost and enforce_ceiling have no reservation concept.
- ledger.py:18 thread lock, :98-104 _connect without timeout, :195-199 lock plus BEGIN IMMEDIATE.
- rls.yml:34 sets DMS_LEDGER_DSN, :45-46 runs only the RLS deny test.
- test_f1_ledger.py:14 POSTGRES_DSN from env, tests at :127, :138, :160.
- tool_runner.py:68 and ponytail/middleware.py:174 already import packs.dms.security.pii and are on the C2 allowlist (tests/contract/test_import_boundaries.py:88,91), so executor.py must not copy that pattern.

ASSUMED:
- Runtime overspend under parallel fan-out (derived from code, not reproduced).
- `database is locked` under heavy multi-process contention with the default 5s timeout (not reproduced).
- The Postgres ledger tests pass against postgres:16 once wired (not run in this lane).


## Coordinator amendment (2026-09-23, before build)

- GH-01: `packs/dms/security/pii.py` matches only the Singapore NRIC/FIN (`[STFGM]\d{7}[A-Z]`). The Malaysian MyKad (`YYMMDD-PB-####`, e.g. `900101-14-5678`) is not matched on its own and is only caught by accident by the broad card or phone patterns. The engine default redactor in `CortexOS/security/redact_port.py` owns the patterns and adds MyKad (with and without dashes). `packs/dms/security/pii.py` reuses the engine patterns (packs may import the engine; the engine never imports packs) and keeps its public API. Write targets for GH-01 therefore add `packs/dms/security/pii.py`.
- Rejected-but-queued follow-ups: EPIC-KEV-LOOP (decision log, outcome labels, calibration report with n>=300 rule, shadow mode) is sequential, not parallel, and runs as the next epic. COST-NODE-ALL and DAG-RESUME-COST queue behind GH-03 (same file).
- Not closed by this epic: issues the discovery lane reported as possibly false-open (#154, #155, #222-#225). STATUS.md entries for #223-#225 deliberately say "Not COMPLETE", so closing them is the owner's call.
