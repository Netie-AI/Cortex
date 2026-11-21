# #440: served SQL ran with no time limit (2026-11-21)

Gate: `CortexOS/execution/submit.py::_execute_sql`, the one place every
engine-served statement runs (EXPLAIN, the value-domain scan, the page count,
the served statement).

## Expected vs actual (main `304bf84`)

- Expected: a statement past `CORTEX_STATEMENT_TIMEOUT_S` (default 30 s) is
  stopped and the ask is refused by name.
- Actual: `_execute_sql` ran `SET statement_timeout=...`. DuckDB 1.x and 2.x
  have no such setting; the CatalogException was swallowed and nothing was
  bounded. The existing test asserted only that the SET was attempted first.

## Repro

`python -m pytest tests/test_execution/test_statement_deadline_440.py::test_must_fail_a_long_fetch_is_stopped -q`
with main's `submit.py`: `DID NOT RAISE SqlGateAbstain`. The long-statement
cases run unbounded for minutes.

## Root-cause class

**A control whose failure is swallowed.** The SET could not work, failed
loudly, and the failure was caught as "not a refusal", so the code read as
bounded and was not. The guard test asserted the attempt, not the effect
(an intermediate artifact, CLAUDE.md s8 in spirit).

## Fix

`StatementDeadline`: a daemon thread waits for the limit, then calls
`interrupt()` on the call's cursor every 50 ms until the call ends (one
interrupt sent between two statements is dropped by DuckDB, measured), and
`stop()` joins it before the cursor is closed. Past the limit, the call raises
`SqlGateAbstain(violations=["STATEMENT_TIMEOUT"])`, also when the last
statement slipped between two interrupts. Measured on DuckDB 1.5.6:
`interrupt()` stops only its own cursor (a sibling on the shared read-only
instance runs on), the cursor is reusable afterwards, and an interrupt sent
while idle does not carry into the next statement.

## Invariants that applied

- Silent fallback is a lie: the swallowed SET.
- Assert the effect, not the attempt: the SET-order test now asserts the
  deadline is armed before the drift scan and no SET is sent.
- Prove a gate can fail: main's code DID NOT RAISE; with the deadline removed
  a statement longer than the limit runs to the end and serves.

## Open, by name

- Pending: the three-attacker round on `1a2fd87` was running when this was written; its results go in PR #481 and in a follow-up to this file.
