# H2-COST-NODE-ALL: every DAG node kind writes a ledger row, including on failure (#252)

- **Date:** 2026-09-24
- **Keywords:** dag_runner, run_dag, cost_ledger, node_executions, ensure_node_record, error row, replayed, step journal, resume, asyncio.gather, audit trail, labels, build_labels, ledger_status_index, ambiguous_ledger_join, kev_calibration_report, H2-COST-NODE-ALL, EPIC-KEV-LOOP, EPIC-HARDEN-2
- **Main idea:** `run_dag` wrote a `node_executions` row for a non-LLM node only after it succeeded, so a failed run's audit trail was missing the node that failed, and a resumed worker recorded nothing for nodes it served from the step journal. Now every kind writes exactly one row per attempt: `ok` on success, `error` (class plus truncated message) when it raises, `replayed` at 0 MYR on a journal replay. A parallel batch settles every sibling before re-raising, so a failed layer leaves one row per node.
- **Verify:** `python -m pytest tests/test_execution/test_cost_node_all.py -q -p no:cacheprovider` (17 tests) then `python -m pytest tests/ -q -p no:cacheprovider`
- **Does not prove:** any Postgres-backed ledger behaviour (in-process `CostLedger` only; the insert SQL is unchanged and `status`/`error` are existing columns); a row for an `llm_judged` node refused before spend (see "Not done"); cross-worker de-duplication of replay rows (a fresh worker writes a `replayed` row even when Postgres already holds the original `ok` row, by design: the replay is evidence of the resume, not a second attempt, and every label consumer must drop it; see "Verifier findings addressed").
- **Cite:** issue #252 acceptance criteria; epic #249; `docs/subagents_findings/2026-09-23_gh-03.md` (replay exemption and the one-lookup rule this ticket builds on).

## Expected vs actual

- Expected (issue #252): when a non-LLM node raises, exactly one `node_executions` row with `status='error'` and the error class/message (truncated) is written, then the exception is re-raised unchanged. A node replayed from the step journal is recorded as `replayed` at 0 MYR, never as a fresh ok spend. No node ever gets two rows for one attempt. GH-03's parallel gate and its tests stay green unmodified.
- Actual before (baseline 275fe13), reproduced with the new tests swapped against the base files:
  - deterministic_rule without a ruleset raises `ValueError`; `records_for_run` holds `[('dref', 'ok')]` only, no row for `chk`.
  - three parallel tool_call siblings with `t2` raising: `records_for_run` holds `t1` and `t3` only (`KeyError: 't2'` when the test looks for its row). With the whole layer in one batch, `asyncio.gather` re-raised on the first failure and `t3` was still in flight; the run had already raised by the time its row landed (`['t1', 't3'] == ['t1', 't2', 't3']` failed).
  - a fresh worker resuming a completed run: `records_for_run` is `[]`; the replayed nodes leave nothing in that worker's ledger.
- Actual after: the same runs raise the same exception type and message, and the ledger holds one row per node that ran: `[('dref', 'ok'), ('chk', 'error')]` with `error='ValueError: Node 'chk': deterministic_rule requires ruleset'`, `tier='deterministic'`, `cost_myr=0.0`, `ceiling_myr` equal to the workflow ceiling; `{t1: ok, t2: error, t3: ok}` for the parallel layer, no emit row; `[('dref', 'replayed'), ('emit', 'replayed')]` with `cost_myr=0.0`, `cache_hit=True` and a run total of 0.0 for the fresh worker.

## Repro

```
git show 275fe13:CortexOS/execution/dag_runner.py > CortexOS/execution/dag_runner.py
git show 275fe13:CortexOS/routing/cost_ledger.py > CortexOS/routing/cost_ledger.py
python -m pytest tests/test_execution/test_cost_node_all.py -q -p no:cacheprovider
# 6 failed, 5 passed
git checkout CortexOS/execution/dag_runner.py CortexOS/routing/cost_ledger.py
```

Fail on base (6):

| Test | Failure on 275fe13 |
|---|---|
| `test_deterministic_rule_that_raises_writes_one_error_row_and_reraises` | `assert 0 == 1` (no row for `chk`) |
| `test_error_text_is_class_plus_message_truncated` | `ImportError: cannot import name 'ERROR_MAX_CHARS'` (imported inside the test so the file still collects) |
| `test_parallel_layer_with_one_failing_tool_call_has_one_row_per_node[None]` | `KeyError: 't2'` |
| `test_parallel_layer_with_one_failing_tool_call_has_one_row_per_node[2]` | `KeyError: 't2'` |
| `test_parallel_batch_settles_every_sibling_before_raising` | `['t1', 't3'] == ['t1', 't2', 't3']` |
| `test_resumed_run_on_fresh_worker_records_replays_at_zero_cost` | `[] == [('dref', 'replayed'), ('emit', 'replayed')]` |

Pass on base (5), kept as regression guards for the "never two rows" and no-false-positive acceptance: `test_resumed_run_on_same_worker_keeps_one_row_per_node` (a same-process resume must not double the original rows now that replays write), `test_llm_node_failure_keeps_the_single_executor_error_row` and `test_llm_node_success_keeps_the_single_executor_ok_row` (the new error path must not add a second row to the LLM path), and `test_healthy_run_of_every_non_llm_kind_has_only_ok_rows[False|True]` (document_ref, three tool_call siblings, rag_retrieve, rag_rerank, rag_answer, a2a_call and emit all complete with one `ok` row each; the no-false-positive corpus).

## What changed

`CortexOS/routing/cost_ledger.py`:

- `ensure_node_record` gained keyword-only `status="ok"`, `error=None`, `started_at=None`, `cache_hit=False`. Defaults keep every existing caller byte-for-byte equivalent (`tests/test_execution/test_cost_ledger_and_executor.py::test_ensure_node_record_skips_when_llm_already_wrote` still passes). `latency_ms` is now derived from `started_at` when given, 0 otherwise. The `has_node_record` guard kept the LLM path to one row; round 3 below narrows it to one attempt (`dedupe_after`), because a whole-history guard dropped a retry's row.
- New `ERROR_MAX_CHARS = 500` and `format_node_error(exc, *, limit)`: `ClassName: message`, truncated with a trailing `...`. Module-level, no new imports.

`CortexOS/execution/dag_runner.py`:

- `_one` takes `started_at = now_utc()` before `execute_node`. On success the existing `ensure_node_record` call passes it through, so ok rows for non-LLM nodes now carry a real latency instead of 0.
- On exception, after the existing `node_error` event and before the unchanged `raise`, every kind except `llm_judged` writes an error row via `ensure_node_record(status="error", error=format_node_error(exc), tier=_node_tier_label(node), cost_myr=0.0, ceiling_myr=wf)`. The write is wrapped in `try/except Exception: pass` so a ledger fault can never replace the original exception. `llm_judged` is excluded because `invoke_routed_completion` owns its rows: it writes ok and error rows on spend and deliberately writes none on a pre-spend refusal (`CostCeilingExceeded`, asserted by GH-03's `test_sequential_keeps_per_node_gate_behaviour`, and `OutboundNotSendable`, asserted by `test_dag_runner.py::test_outbound_node_blocked_when_not_sendable`; both are outside this ticket's write targets and must stay green). For `agent_task`, which logs LLM rows under the same node id, the guard keeps it to one row.
- On a journal replay, `ensure_node_record(status="replayed", cost_myr=0.0, cache_hit=True)` is called before the `node_done` event, also fault-tolerant. Because it goes through the guard, a worker that already holds the original `ok` row (same-process resume, which is what GH-03's resume tests exercise) adds nothing, and a fresh worker records the replay at 0 MYR.
- The parallel branch now runs `asyncio.gather(..., return_exceptions=True)`, then re-raises the first exception in batch order. Before, `gather` re-raised on the first failure while the other siblings kept running detached, so their rows could land after the run had raised, and the audit trail of a failed batch depended on scheduling. GH-03's batch gate and its `_journal_lookup` sharing are untouched; the eight tests in `test_parallel_cost_ceiling.py` pass unmodified.
- `_TIER_LABELS` / `_node_tier_label(node)` map a node kind to the tier label its success path would have reported (`deterministic`, `emit`, `tool`, `rag`, `a2a`, `agent`), used only for error rows because a node that raised never returned a `NodeResult`.

`tests/test_execution/test_cost_node_all.py` (new, 11 tests). Every test asserts on the run result and `records_for_run`. The tool_call tests monkeypatch `netie.execution.tool_runner.run_tool_call` so a named tool raises and the rest return an ok payload; nothing touches the F1 ledger or the real tool allowlist. The resume tests point `step_journal.DEFAULT_DB` at `tmp_path`.

## Verifier findings addressed (round 2)

### F1: `replayed` rows broke the KEV calibration label join

**Claim.** `build_labels` (`CortexOS/decision/labels.py`, EPIC-KEV-LOOP #245) pairs decision-log rows with `node_executions` rows ordinally per `(run_id, node_id)`, and `ledger_status_index` kept every row regardless of status. An `llm_judged` node that ran once (one log row) and was then replayed by a fresh worker (one `ok` row plus one `replayed` row) had counts 1 vs 2 and was excluded as `ambiguous_ledger_join`. Before this ticket the row was labelled.

**Reproduced.** `/tmp/p252/probe.py` (verifier's probe, run from the worktree with `PYTHONPATH=$PWD`) on 6df1d09:

```
node_executions rows: [('j1','ok',0.5),('emit','ok',0.0),('j1','replayed',0.0),('emit','replayed',0.0)]
labels rows 0 excluded {'ambiguous_ledger_join': 1}
pre-#252 (no replayed row) labels rows 1 excluded {}
```

Confirmed also through the operator artifact: `scripts/kev_calibration_report.py --log ... --ledger-jsonl ...` with that ledger printed `n=0`, `excluded ambiguous_ledger_join=1`.

**Root cause.** The ordinal join assumes every ledger row for a key is one attempt with one matching log row. A replay is not an attempt: `dag_runner` writes it without calling the adapter, without a routing decision and without a decision-log entry. The two truths in #252 (a replay leaves evidence in the ledger) and #245 (ledger rows are attempts) collided; neither side filtered.

**Fix (root cause, in the join, not the writer).** `CortexOS/decision/labels.py`:

- New `LEDGER_NON_ATTEMPT_STATUSES = frozenset({"replayed"})`, exported.
- `ledger_status_index` skips any record whose `status` is in that set before building the per-key list, so a replay never consumes a log row and never makes a key ambiguous. Keyed on `status` only: the ledger writes `cache_hit=True` on the same row, but `cache_hit` is not a status and a future cached-but-real call must not be dropped by accident.
- Module and function docstrings state the rule.

Kept: the replay row itself (0 MYR, `cache_hit=True`, `status='replayed'`) exactly as #252 writes it. The ledger is the audit trail and is right to hold it; the label consumer is the one that must know a replay is not a decision. Not chosen: dropping the row from the ledger (loses the resume audit) or relabelling it `ok` (a false spend row, which `test_resumed_run_on_fresh_worker_records_replays_at_zero_cost` forbids).

**Scope.** `CortexOS/decision/labels.py` was not a listed write target; it is added to this ticket's scope because the regression is caused by this ticket's new status value and the fix is a one-line filter with no other owner (#245 has landed).

**Tests** (in `tests/test_execution/test_cost_node_all.py`, the ticket's test file; 3 new, 14 total):

| Test | Asserts on | On base `labels.py` (`git show 275fe13:CortexOS/decision/labels.py`, identical to 6df1d09's) |
|---|---|---|
| `test_resumed_llm_run_on_fresh_worker_still_labels_its_one_decision` | runs `_llm_dag` on ledger 1, resumes on a fresh ledger 2 with the journal, feeds the concatenated rows (`[j1 ok, emit ok, j1 replayed, emit replayed]`) plus the one log entry to `build_labels`; expects `excluded == {}` and one row `(run_id, 'j1', label 1, source 'ledger_status')`, `adapter.calls == 1` | `assert {'ambiguous_ledger_join': 1} == {}` |
| `test_replayed_ledger_row_never_consumes_a_decision_log_row` | `[ok, replayed]` and `[replayed, ok]` plus one entry -> 1 labelled row; `[error, replayed]` -> label 0 (the replay never stands in for the attempt); `[ok, ok]` plus one entry is still `ambiguous_ledger_join` (existing rule unchanged) | `assert {'ambiguous_ledger_join': 1} == {}` |
| `test_kev_calibration_report_counts_a_resumed_node_with_replay_rows` | subprocess run of `scripts/kev_calibration_report.py --log --ledger-jsonl` with the four-row ledger; expects `entries_read=1`, `n=1`, `excluded none=0`, no `excluded ambiguous_ledger_join` line | `'n=1' not in [... 'excluded ambiguous_ledger_join=1', ... 'INSUFFICIENT: n=0 < 300 ...']` |

Swap/run/restore record: `3 failed, 11 passed` with the base file, `14 passed` after restore (the swap overwrote the working file, so the fix was re-applied from the diff and the probe re-run: `labels rows 1 excluded {}`).

**Gates after the fix.** Full suite `2375 passed, 13 skipped, 4 xfailed` (2372 + 3 new); `ruff check CortexOS packages/cortex_contract scripts tests/packaging tests/contract tests/test_execution/test_cost_node_all.py` clean; `lint-imports` 3 kept, 0 broken; `tests/contract` + `tests/test_decision` 206 passed; `mypy CortexOS/decision/labels.py` clean; `scripts/check_versions.py` OK. `tests/test_decision/test_labels_and_report.py` (#245's own gate) unmodified and green.

**Does not prove.** That every other consumer of `node_executions` filters `replayed`: `CostLedger.total_cost` is unaffected (0 MYR), and `kev_calibration_report` is the only label consumer today. Any new reader that counts rows as attempts must import `LEDGER_NON_ATTEMPT_STATUSES`.

## Judge finding addressed (round 3): dedupe per attempt, not per node

**Claim.** On a same-worker resume (`POST /api/workflows/resume` in `CortexOS/api/workflow_routes.py` reuses `app.state.ledger` and the same `run_id`), a node that failed and then succeeds on the retry keeps only its stale `error` row: the retry's `ok` row is dropped, because `ensure_node_record` skipped whenever `has_node_record(run_id, node_id)` was true over the node's *whole* history. Base (275fe13) wrote no error row, so its retry's `ok` row landed; 6df1d09/3fd00ca made the audit trail say the node failed when it succeeded.

**Expected vs actual.**
- Expected: one row per attempt. Parallel tool layer with `t2` raising, then a same-ledger resume after the failure is cleared: `t2` rows `[error (attempt 1), ok (attempt 2)]`; `t1`/`t3`/`emit` one `ok` each (replays on the worker that holds the originals add nothing). A second consecutive failure: two `error` rows.
- Actual on 3fd00ca (new tests run against `git show 3fd00ca:` of `dag_runner.py` and `cost_ledger.py`): `t2 == [('error', '_BoomTool: tool t2 exploded')]` (no ok row, while the run returned t2's retry output), and `[('error', '_BoomTool: first')]` for the double failure (second error lost).
- Actual after: exactly the expected rows.

**Root cause.** The de-duplication key was `(run_id, node_id)` but the thing being de-duplicated is an *attempt*. The guard existed to stop the dag_runner's row doubling the row `invoke_routed_completion` writes during the same attempt; applied over whole history it also swallowed later attempts. Class: idempotency key coarser than the event it guards.

**Fix.**
- `CostLedger.node_record_count(run_id, node_id)` (new) and `ensure_node_record(..., dedupe_after: int | None = None)`. With `dedupe_after` the write is skipped only when the node's row count has grown past the watermark, i.e. a row was appended *during this attempt*. Without it the whole-history guard is unchanged (default for existing callers and for replays).
- `dag_runner._one` takes `rows_before = ledger.node_record_count(...)` right after `started_at`, before `execute_node`, and passes `dedupe_after=rows_before` on both the ok and the error write. The replay write keeps the whole-history guard: a worker holding the original row adds nothing; a fresh worker records `replayed`.
- Design decision: a row-count watermark instead of the suggested `dedupe_since=started_at` timestamp. Same semantics, but it does not depend on the wall clock: with coarse clock resolution (Windows) a fast retry could share `started_at` with the previous attempt's row and be skipped again, and a clock step backwards would do the same. `_records` is append-only, so the count is monotonic per worker. Nodes with the same `node_id` never run concurrently within one run, so the watermark cannot be advanced by a sibling.

**KEV-CALIB pairing.** `build_labels` pairs ordinally per `(run_id, node_id)`; multiple rows per node are its normal case (one per attempt). The LEDGER_NON_ATTEMPT_STATUSES filter from round 2 is kept. New test `test_llm_node_retry_on_same_ledger_has_one_row_per_attempt`: an `llm_judged` node fails (executor error row), is resumed on the same ledger and succeeds: rows `[j1 error, j1 ok, emit ok]`, `adapter.calls == 2`, total 0.5 MYR, and two decision-log rows label as `[(j1, 0), (j1, 1)]` with nothing excluded. It passes on 3fd00ca too (the executor, not the dag_runner, owns LLM rows); it is the regression guard that the narrower guard does not double an LLM attempt. `tests/test_decision` green.

**Tests** (3 new, 17 total in `tests/test_execution/test_cost_node_all.py`):

| Test | On 3fd00ca |
|---|---|
| `test_same_ledger_resume_after_failed_tool_records_error_then_ok` | FAIL: `[('error', ...)] == [('error', ...), ('ok', None)]` |
| `test_same_ledger_resume_that_fails_again_records_two_error_rows` | FAIL: `[('error', '_BoomTool: first')] == [... ('error', '_BoomTool: second')]` |
| `test_llm_node_retry_on_same_ledger_has_one_row_per_attempt` | pass (regression guard, see above) |

Swap/run/restore: `2 failed, 15 passed` with 3fd00ca's two source files, `17 passed` after restore.

**Gates.** Full suite `2378 passed, 13 skipped, 4 xfailed` (rc=0); `ruff check` on the four changed code/test files clean; `lint-imports` 3 kept, 0 broken (rc=0); `tests/contract` 95 passed (rc=0); `tests/test_decision` + this file 128 passed; `mypy` no errors in `cost_ledger.py`/`dag_runner.py`; `scripts/check_versions.py` OK.

**Verified vs assumed.** Verified in-process (`CostLedger` without an engine) by driving `run_dag` twice on one ledger with `resume=True`, which is what the resume route does with `app.state.ledger`. Assumed: the HTTP route itself adds nothing that changes this (not exercised end to end here); with a Postgres engine the watermark counts only this worker's in-process rows, which is what the guard needs (it only has to see rows written during the current attempt by this same process).

## Root-cause class

Incomplete audit coverage on the failure path: the ledger write sat after the call that could raise, so the write was skipped exactly when it mattered. Same class for replays: the write depended on the in-process cache holding a row from a previous process. Fixed by making the row write unconditional per attempt (ok, error or replayed) and de-duplicated by the existing guard, not by adding a second ledger.

## Which invariant applies

- One ledger: rows still go through `CostLedger.add`; no new hash chain, no new table, no new columns. `status` and `error` are existing `node_executions` columns (`CortexOS/db/sql/node_executions.sql`, not touched).
- Fail closed, in the audit sense: a node that raises now leaves evidence; the exception itself is re-raised unchanged so no caller sees a different outcome.
- Do-not-touch respected: `executor.py`, `model_router.py`, `judgment_model.py`, `CortexOS/db/sql/**`, `tests/contract/**`, `tests/invariants/**`, `.importlinter`, `contract/**` unchanged. `dag_runner.py` gained no new module imports beyond two names from `cost_ledger`, which it already imported from; `lint-imports` 3 kept, 0 broken.

## Verified vs assumed

Verified:
- The three acceptance scenarios and the truncation rule, on the fixed files (11 passed) and against the base files (6 failed, 5 passed, table above).
- Full suite: 2372 passed, 13 skipped, 4 xfailed (baseline 2361 passed + 11 new). `ruff check` clean on the three changed files. `mypy`: 41 errors in 26 files, all pre-existing, none in the changed files (a first cut had one new error on the `gather` result narrowing; fixed with an explicit typed list). `lint-imports`: 3 kept, 0 broken. `tests/contract`: 95 passed. `tests/test_execution/`: all green including the manifest corpus.
- GH-03 tests (`test_parallel_cost_ceiling.py`, 8) and `test_dag_runner.py` (8) unmodified and green.

Assumed:
- Postgres insert of `status='replayed'` works: the column is `TEXT NOT NULL` with no check constraint, and the insert SQL is unchanged, but no Postgres run was made here.
- A `replayed` row from a fresh worker alongside the original `ok` row in Postgres is the intended shape (two attempts, two rows). `fetch_records_for_run` merges on `(node_id, started_at)`, so the two are distinguishable.
- The no-false-positive corpus covers every non-LLM kind the issue lists except `deterministic_rule` on its success path, which `test_dag_runner.py` already covers with `spa_v1.yaml`.

## Not done

- An `llm_judged` node that raises before `invoke_routed_completion` (a `tier_pair` `ValueError`, or `OutboundNotSendable`) still leaves no row. Writing one would break the existing pre-spend-refusal convention pinned by tests outside this ticket's write targets; if that convention is to change it needs its own ticket touching `executor.py`.
- No event emitted for the replay row beyond the existing `node_done` with `replayed: True`.
- Pre-existing `mypy` errors (41) are outside this ticket.
