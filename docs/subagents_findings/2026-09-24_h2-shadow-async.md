# H2-SHADOW-ASYNC: shadow evaluation off the serving path, bounded and counted (#253)

- **Date:** 2026-09-24
- **Keywords:** kev, shadow mode, async, bounded queue, worker thread, dropped, flush, atexit, serving latency, grace, httpx client, CORTEX_KEV_SHADOW_QUEUE, CORTEX_KEV_SHADOW_GRACE
- **Main idea:** `ShadowEvaluator.observe` no longer evaluates kev inline. It queues the observation on a bounded FIFO drained by one daemon thread and returns; a full queue drops and counts (`dropped()`, `summary()["dropped"]`), never blocks, never raises. `flush(timeout)` drains for readers and at interpreter exit; `read_rows`, `summary`, `write_failures` and `last_write_error` flush first. A kev that sleeps 2 s per call now costs the served decision 0.10 s (the idle-worker grace) instead of 4.0 s, and the served decision, the row content and the prompt-free guarantee are unchanged.
- **Verify:** `python -m pytest tests/test_decision/test_shadow_async.py tests/test_decision/test_shadow.py -q -p no:cacheprovider` (22 new + 54 unmodified), then the full suite.
- **Does not prove:** any live kev server; any agreement number; behaviour under a real multi-process server (the fork hook resets the worker in the child and is exercised only by reasoning, not a test).

Epic #249 EPIC-HARDEN-2. Base 275fe13. Worktree lane wf_41d6fc93-b7a-4.

## Expected vs actual

Expected (issue #253): the served decision returns without waiting for kev; the
evaluation runs on a background worker with a bounded queue; a full queue drops and
counts; `flush(timeout)` and process exit drain; `test_shadow.py` passes unmodified.

Actual on 275fe13: `JudgmentModel.decide()` called `shadow.observe()` inline, and
`observe` ran `decide(check_order=True)` synchronously, two kev calls with a 5 s
timeout each. Measured with a backend that sleeps 2 s per call (scratch probe,
`base_probe.py`, base `shadow.py` swapped in): `decide()` took 4.001 s and returned
the rules decision. With the new module: 0.101 s, same decision.

A second cost was hidden under the first: `KevHttpBackend.evaluate` calls
`httpx.post`, which builds a fresh `httpx.Client` (and a TLS context) per call.
Measured here: 55 to 80 ms per client, so about 105 ms per shadow row even with a
respx mock that answers instantly. On base that 105 ms was paid on the serving path
for every decision while shadow was on, kev healthy or not.

## What was built

`CortexOS/decision/shadow.py` only (plus the new test file and this note):

- `ShadowWorker(maxsize)`: a bounded `queue.Queue`, one lazily started daemon thread
  (`kev-shadow`), a condition variable tracking `pending` (submitted but not yet
  finished, so `flush` waits for the in-flight item, not just an empty queue).
  `submit(fn, grace)` never blocks on the queue: `queue.Full` increments
  `worker.dropped` and the process-wide `dropped()` counter and returns False. An item
  that raises is counted through `write_failures()` (`worker: ...`) and the thread
  keeps draining. `flush(timeout)` returns True when drained, False on timeout, and
  starts a thread if none is alive. `os.register_at_fork(after_in_child=...)` resets
  the default worker's queue, condition and thread in a forked child, so the child
  never waits on parent items nobody will run and never inherits a held lock.
- Grace: `submit` waits up to `grace` seconds for the item only when the worker was
  idle at submit time (this item is the sole pending one). Behind any backlog it
  returns at once. Default `DEFAULT_GRACE = 0.1` s, env `CORTEX_KEV_SHADOW_GRACE`,
  constructor `grace=`. Effect: a healthy loopback kev (about 1 ms with a persistent
  client) leaves the row on disk in decision order before the decision is served, and
  a hung kev costs at most 0.1 s once, then nothing while the backlog exists.
- Persistent client: `ShadowEvaluator.__init__` rebuilds a clientless
  `KevHttpBackend` through its public constructor with `client=httpx.Client()`
  (same `base_url`, `model`, `timeout_s`, `server_calibrated`), so the single worker
  thread reuses one client. Any other backend, or one already carrying a client, is
  left alone. Row `backend` stays `kev-http`.
- `observe(state, rules_tier) -> bool`: captures the target path on the calling
  thread (env is read where the decision happens), submits a closure, counts a drop
  on `evaluator.dropped`. It returns True when queued and False when dropped; the
  previous return value (the row dict) had no consumer besides the old tests, and
  `JudgmentModel.decide` ignores it.
- `flush(timeout=30)` module-level and `ShadowEvaluator.flush`. `atexit` registers a
  10 s flush so a process that exits with observations queued still writes them.
- Readers flush first: `read_rows`, `summary` (through `read_rows`), `write_failures`,
  `last_write_error`. This is what keeps `test_shadow.py` green unmodified.
- `summary()` adds `"dropped": n` once any observation was dropped in this process.
  The key is absent at zero because
  `test_summary_on_missing_file_is_empty_and_insufficient` asserts the exact dict
  shape and may not be edited; `dropped()` is always available as a function.
- Counters: `observations` and `dropped` on the evaluator are now lock-protected
  (the KEV-SHADOW verifier's "unlocked observations counter" note). `queue_size()`
  reads `CORTEX_KEV_SHADOW_QUEUE` (default 256, floor 1).

Not changed: row fields, `cause_category`, `abstain_reason_category`, `append_row`,
the forbidden-key check, `JudgmentModel`, `decision_log`, `labels`, `backends`.

## Repro

```
# base (275fe13) shadow.py, backend sleeps 2 s per call:
python base_probe.py <repo>        # decide() took 4.001 s; has flush: False
# this change:
python base_probe.py <repo>        # decide() took 0.101 s; has flush: True
```

Both runs served `JudgmentDecision(tier=T1, confidence=0.7, reason='heuristic routing fallback')`.

## Root-cause class

Side effect on the request path with an unbounded external dependency: an
observation-only call shared the caller's thread and inherited kev's timeouts.
Class: "telemetry coupled to serving latency". Secondary: per-call client
construction in the backend (a resource that should be pooled was rebuilt per use).

## Which invariant applies

- Served decision invariance: every new test compares the served decision to a
  separate `JudgmentModel().rules_decide(req)` and asserts `"kev" not in reason`,
  under a slow kev, a blocked kev, a full queue, 16 concurrent threads and 300
  sequential decisions.
- Serving latency bound: `decide()` under a 2 s kev returns in under 0.2 s (measured
  0.10 s, the grace); behind a backlog under 0.05 s per decision.
- Never blocks, never raises: a queue of 2 behind one in-flight item drops exactly 2
  of 4 further observations in under 0.2 s total, `write_failures()` stays 0 (a drop is
  not a failure), and `rows + dropped == decisions` (3 + 2 = 5).
- Drains: `flush(0.2)` behind a blocked kev returns False with `pending() == 1`, then
  True after release; a subprocess that exits without calling `flush` behind a 0.3 s
  kev leaves the row on disk.
- Prompt-free file: the planted secret is in every concurrent request's content and
  in the subprocess's content; both files are grepped for it.
- Concurrency: 16 threads x 50 decisions with a queue of 32 all resolve (wall under
  10 s here; about 0.2 s measured), `len(rows) + dropped == 800`,
  `observations == len(rows)`, every line strict JSON.
- No false positive: 300 sequential decisions against a healthy kev drop nothing,
  fail nothing, 600 kev calls, `summary()` reports `n=300`, `sufficient=True`,
  `agreement_rate=1.0` and no `dropped` key. The queue must not shed ordinary traffic.
- No metric below n=300: unchanged; the one rate this note reports is at n=300 in a
  test against a mock, and it is a test of the counter, not a claim about kev.
- C2 boundary: `shadow.py` imports stdlib, `httpx` (lazily, only to build a client for
  a `KevHttpBackend`), `CortexOS.paths` and sibling `CortexOS.decision` modules.
  `lint-imports` 3 kept, 0 broken; `tests/contract` 95 passed.
- Do-not-touch respected: `judgment_model.py`, `decision_log.py`, `labels.py`,
  `CortexOS/execution/**`, `tests/test_decision/test_shadow.py`, `tests/contract/**`,
  `tests/invariants/**`, `.importlinter`, `contract/**` are untouched
  (`git diff --stat 275fe13` shows only `shadow.py`, the new test file and this note).

## Design decisions worth knowing

- Why a grace at all: three frozen tests in `test_shadow.py` read the raw file with
  `Path.read_text` immediately after `decide()` and assert one row is present
  (`test_non_finite_kev_numbers_are_dropped_and_file_stays_strict_json`) or that the
  file exists (the two echo tests). No public API sits between `decide()` and that
  read, so a purely fire-and-forget design fails them, and the ticket forbids editing
  them. The idle-only grace makes the fast-kev case land the row before the decision
  returns while keeping the slow-kev cost bounded at 0.1 s once per backlog. It is
  also operationally useful: with a healthy kev the file stays in decision order and a
  crash loses nothing.
- Why the persistent client: without it the mocked evaluation took about 105 ms
  (two client constructions), above the 0.1 s grace, and the NaN test failed 3 of 3
  runs. With it the evaluation takes about 1 ms and the frozen suite passed 3 of 3
  runs. Raising the grace instead would have pushed the served-path bound toward the
  ticket's 0.2 s limit. The rebuild goes through `KevHttpBackend`'s public
  constructor; the only private read is `_client is None` to detect a clientless one.
- One worker thread, not a pool: order of rows equals order of decisions for a
  single caller, which `test_shadow.py` asserts, and shadow rows are not latency
  sensitive. Throughput is bounded by kev, not by the thread.
- Process-wide `dropped()` rather than per-file: a drop is a decision that never
  produced a row, so it cannot live in the file; it is reported beside `n` and reset
  by `reset_dropped()`. The test fixture resets it and flushes the default worker on
  teardown so no test's work bleeds into the next.

## Verified vs assumed

VERIFIED:
- New file `tests/test_decision/test_shadow_async.py`: 22 passed on this change.
- On base: with `CortexOS/decision/shadow.py` replaced by
  `git show 275fe13:CortexOS/decision/shadow.py` (then restored, `cmp` clean), the
  file fails at collection (`ImportError: cannot import name 'ShadowWorker'`), so all
  22 error on base. The semantic failure behind the import failure is the probe
  above: base `decide()` takes 4.001 s against a 2 s kev, which
  `test_slow_kev_does_not_delay_served_decision` bounds at 0.2 s, and base has no
  `flush`, `dropped`, `reset_dropped`, `ShadowWorker`, `queue_size` or
  `grace_seconds`, which the remaining tests call.
- `tests/test_decision/test_shadow.py` unmodified: 54 passed, 3 of 3 runs.
- `tests/test_decision/`: 133 passed.
- ruff on the two changed files: clean. mypy: 41 errors in 26 files, the stated
  baseline, none in `shadow.py` or the new test file. `lint-imports`: 3 kept, 0
  broken. `tests/contract`: 95 passed.
- Full suite: 2383 passed, 13 skipped, 4 xfailed, rc=0 (exit code appended to the
  run's own log file, not read through a pipe). Baseline on 275fe13 is 2361 passed;
  the difference is the 22 new tests.

ASSUMED:
- The fork hook is correct by reading `os.register_at_fork` semantics; no test forks a
  process with a busy worker. H2-DLOG-MP (a parallel lane of #249) owns the
  multi-process story for the decision log; the shadow file is one `write()` per row under a lock as before.
- The 0.1 s grace is enough for a real loopback kev only if kev answers in well under
  50 ms per call; if it does not, rows land after the decision (via the worker) and
  readers still see them after `flush`, so nothing is lost, only the ordering guarantee
  for a single caller weakens.
- `atexit` runs before daemon threads are torn down in CPython, so the exit flush can
  complete; verified by the subprocess test on 3.11 here, not on other interpreters.

## Not done

- No change to `KevHttpBackend` itself (not a write target); the per-call client
  construction still costs the serving backend about 105 ms per decision when kev
  serves rather than shadows. Worth its own ticket.
- `summary()["dropped"]` is process-local. An operator reading the file from another
  process sees no drops; a persisted drop marker would need a row schema change.
