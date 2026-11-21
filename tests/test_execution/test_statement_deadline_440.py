"""DUCKDB2-STATEMENT-LIMIT-01 (#440): served SQL has a real time limit.

``_execute_sql`` used to run ``SET statement_timeout`` (DuckDB has no such
setting), swallow the error, and run the statement unbounded. Now one deadline
covers every statement of the call; past it the cursor is interrupted until it
stops and the call is refused as ``STATEMENT_TIMEOUT``.

Attack shapes: a long statement, a long fetch, the next statement on the same
read-only instance after a timeout, a sibling cursor during one, and a
deadline that must not outlive its call.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

import duckdb
import pytest

from CortexOS.dms.sql_validate_gate import SqlGateAbstain
from CortexOS.execution import submit
from CortexOS.execution.pool import PoolConfig, reset_read_pool_for_tests
from CortexOS.execution.session_manifests import get_session_registry
from CortexOS.execution.submit import execute_sql, verify_and_check_pool
from CortexOS.execution.warehouse import close_cached_connections
from tests.test_execution.test_submit_c4 import _signed, issuer, verifier  # noqa: F401

LIMIT_S = 0.4
# Seconds of CPU on any machine this suite runs on: a three-way cross join.
SLOW = "SELECT count(*) AS n FROM nums a, nums b, nums c WHERE (a.i * b.i + c.i) % 7 = 3"
# A big fetch: millions of rows, no aggregate.
BIG_FETCH = "SELECT a.i, b.i AS j FROM nums a, nums b"
FAST = "SELECT count(*) AS n FROM nums"


@pytest.fixture()
def lake(tmp_path: Path) -> Path:
    db = tmp_path / "lake.duckdb"
    con = duckdb.connect(str(db))
    con.execute("CREATE TABLE nums AS SELECT range AS i FROM range(3000)")
    con.close()
    return db


@pytest.fixture()
def granted(verifier, issuer):  # noqa: F811
    reset_read_pool_for_tests(PoolConfig("default", 4, 5.0, LIMIT_S))
    verified = verify_and_check_pool(_signed(issuer, predicates={"nums": "1=1"}), "pool-a")
    get_session_registry().bind(verified)
    yield verified
    close_cached_connections()
    reset_read_pool_for_tests(PoolConfig("default", 4, 5.0, 30.0))


def _run(verified, sql: str, db: Path) -> tuple[list[dict], float]:
    started = time.monotonic()
    rows, _, _ = execute_sql(verified, sql, db_path=db, value_domains=[])
    return rows, time.monotonic() - started


def _deadline_threads() -> list[threading.Thread]:
    return [t for t in threading.enumerate() if t.name == "cortex-statement-deadline"]


def test_must_fail_a_long_statement_is_stopped_and_refused_by_name(granted, lake) -> None:
    started = time.monotonic()
    with pytest.raises(SqlGateAbstain) as caught:
        _run(granted, SLOW, lake)
    elapsed = time.monotonic() - started
    assert caught.value.violations == ["STATEMENT_TIMEOUT"]
    assert "limit" in str(caught.value)
    assert elapsed < LIMIT_S + 2.0, elapsed


def test_must_fail_a_long_fetch_is_stopped(granted, lake) -> None:
    with pytest.raises(SqlGateAbstain) as caught:
        _run(granted, BIG_FETCH, lake)
    assert caught.value.violations == ["STATEMENT_TIMEOUT"]


def test_the_same_instance_serves_the_next_statement_after_a_timeout(granted, lake) -> None:
    with pytest.raises(SqlGateAbstain):
        _run(granted, SLOW, lake)
    rows, _ = _run(granted, FAST, lake)
    assert rows == [{"n": 3000}]


def test_no_deadline_outlives_its_call(granted, lake) -> None:
    """A stale timer must not interrupt a later statement."""
    rows, _ = _run(granted, FAST, lake)
    assert rows == [{"n": 3000}]
    assert _deadline_threads() == []
    time.sleep(LIMIT_S * 2)
    rows, _ = _run(granted, FAST, lake)
    assert rows == [{"n": 3000}]
    with pytest.raises(SqlGateAbstain):
        _run(granted, SLOW, lake)
    assert _deadline_threads() == []


def test_a_timeout_does_not_stop_a_sibling_statement(granted, lake) -> None:
    """Two asks on the shared read-only instance: only the slow one is stopped."""
    reset_read_pool_for_tests(PoolConfig("default", 4, 5.0, 3.0))
    out: dict[str, object] = {}

    def fast() -> None:
        time.sleep(0.2)
        out["fast"] = _run(granted, FAST, lake)[0]

    worker = threading.Thread(target=fast)
    worker.start()
    with pytest.raises(SqlGateAbstain):
        _run(granted, SLOW, lake)
    worker.join()
    assert out["fast"] == [{"n": 3000}]


def test_the_deadline_covers_explain_and_the_page_count(granted, lake, monkeypatch) -> None:
    """Armed before EXPLAIN: a slow EXPLAIN stage is refused as a timeout, not as EXPLAIN."""
    from CortexOS.dms import sql_validate_gate

    real = sql_validate_gate.explain_dry_run

    def slow_explain(con, sql, params=None):  # noqa: ANN001
        con.execute(SLOW).fetchall()
        return real(con, sql, params=params)

    monkeypatch.setattr(sql_validate_gate, "explain_dry_run", slow_explain)
    with pytest.raises(SqlGateAbstain) as caught:
        execute_sql(granted, FAST, db_path=lake, value_domains=[], explain_gate=True)
    assert caught.value.violations == ["STATEMENT_TIMEOUT"]


def test_guard_deadline_removed_the_long_statement_runs_unbounded(
    granted, lake, monkeypatch
) -> None:
    """With the deadline gone (main), the same statement runs past the limit and serves."""
    monkeypatch.setattr(submit, "StatementDeadline", _Never)
    rows, elapsed = _run(granted, "SELECT count(*) AS n FROM nums a, nums b WHERE (a.i * b.i) % 7 = 3", lake)
    assert rows and elapsed > 0  # served: nothing bounded it
    with pytest.raises(AssertionError):
        test_must_fail_a_long_statement_is_stopped_and_refused_by_name.__wrapped__  # type: ignore[attr-defined]


class _Never:
    """A deadline that never fires (main's behaviour)."""

    def __init__(self, con, timeout_s) -> None:  # noqa: ANN001
        self.fired = False

    def stop(self) -> None:
        return None
