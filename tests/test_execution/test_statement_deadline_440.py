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
# Longer than LIMIT_S, short enough to run to the end in the guard test.
MEDIUM = (
    "SELECT count(*) AS n FROM nums a, nums b, (SELECT i FROM nums WHERE i < 15) c "
    "WHERE (a.i * b.i + c.i) % 7 = 3"
)


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
    """With the deadline gone (main), a statement longer than the limit runs to
    the end and serves: nothing bounds it."""
    monkeypatch.setattr(submit, "StatementDeadline", _Never)
    rows, elapsed = _run(granted, MEDIUM, lake)
    assert rows and rows[0]["n"] > 0
    assert elapsed > LIMIT_S * 1.5, elapsed


class _Never:
    """A deadline that never fires (main's behaviour)."""

    def __init__(self, con, timeout_s) -> None:  # noqa: ANN001
        self.fired = False

    def restart(self) -> None:
        return None

    def stop(self) -> None:
        return None


# ── attacker round on 1a2fd87 ───────────────────────────────────────────────


class _Interrupted:
    """A cursor whose statement the deadline interrupted."""

    def execute(self, sql: str):  # noqa: ANN201
        raise duckdb.InterruptException("INTERRUPT Error: Interrupted!")


def test_must_fail_an_interrupted_column_probe_is_not_read_as_absent() -> None:
    """The BYPASS: ``_has_column`` read every error as "no such column", so an
    interrupt landing on its ``LIMIT 0`` probe was cached per instance as
    "nothing to map" and the stale-lake check went off for every later ask."""
    from CortexOS.ontology import value_domain

    with pytest.raises(duckdb.InterruptException):
        value_domain._has_column(_Interrupted(), "t", "k0")


def test_must_pass_a_missing_column_is_still_absent(lake) -> None:
    from CortexOS.ontology import value_domain

    con = duckdb.connect(str(lake), read_only=True)
    try:
        assert value_domain._has_column(con, "nums", "i") is True
        assert value_domain._has_column(con, "nums", "no_such_col") is False
        assert value_domain._has_column(con, "no_such_table", "i") is False
    finally:
        con.close()


def test_must_fail_an_interrupted_drift_scan_caches_nothing(tmp_path: Path) -> None:
    """End to end on the cache: an interrupted scan leaves no entry, so the next
    ask on the same instance scans again and is refused on the stale lake."""
    from CortexOS.ontology import value_domain
    from CortexOS.ontology.value_domain import ValueDomain

    db = tmp_path / "stale.duckdb"
    con = duckdb.connect(str(db))
    con.execute("CREATE TABLE t AS SELECT 'b' AS k0 FROM range(50)")
    con.close()
    domain = ValueDomain(table="t", column="k0", canonical=("BETA",), sources=(("b", "BETA"),))
    value_domain._LAKE_CACHE.clear()
    with pytest.raises(duckdb.InterruptException):
        value_domain.lake_drift(_Interrupted(), [domain], instance="inst-1")
    assert value_domain._LAKE_CACHE == {}
    real = duckdb.connect(str(db), read_only=True)
    try:
        drift = value_domain.lake_drift(real, [domain], instance="inst-1")
    finally:
        real.close()
        value_domain._LAKE_CACHE.clear()
    assert drift, "the stale lake must still be found after an interrupted scan"


# A fast page over a table whose full count is slow: the count is optional.
FAST_PAGE_SLOW_COUNT = "SELECT a.i AS i FROM nums a, nums b, nums c WHERE (a.i * b.i + c.i) % 7 = 3 LIMIT 1"


def test_must_fail_a_slow_page_count_does_not_refuse_a_fast_page(granted, lake) -> None:
    """One deadline used to cover the count and the statement: a count that ran
    out left the served page no time, and a fast answer was refused."""
    rows, _ = _run(granted, FAST_PAGE_SLOW_COUNT, lake)
    assert len(rows) == 1


def test_guard_and_execute_has_the_same_limit(lake) -> None:
    """The legacy serve path had no limit at all."""
    from CortexOS.dms import sql_guardrail

    reset_read_pool_for_tests(PoolConfig("default", 4, 5.0, LIMIT_S))
    con = duckdb.connect(str(lake), read_only=True)
    semantic = {"tables": {"nums": {"columns": {"i": {"type": "BIGINT"}}}}}
    try:
        started = time.monotonic()
        result, rows, entry = sql_guardrail.guard_and_execute(SLOW, semantic, con)
        elapsed = time.monotonic() - started
    finally:
        con.close()
        reset_read_pool_for_tests(PoolConfig("default", 4, 5.0, 30.0))
    if not result.passed and result.violations != ["STATEMENT_TIMEOUT"]:
        pytest.fail(f"precondition: the guard refused for another reason {result.violations}")
    assert result.violations == ["STATEMENT_TIMEOUT"]
    assert rows == [] and result.safe_sql is None and entry.passed is False
    assert elapsed < LIMIT_S + 2.0, elapsed


def test_must_fail_the_submit_envelope_names_the_timeout_and_serves_no_rows(
    granted, lake, issuer, monkeypatch  # noqa: F811
) -> None:
    """What ``POST /v1/contract/submit`` returns: a named refusal, no output."""
    from cortex_contract.execution import PoolSpec, SubmitRequest

    monkeypatch.setattr(submit, "DEFAULT_DB", lake)
    result = submit.submit_request(
        SubmitRequest(
            pool=PoolSpec(id="pool-a"),
            plan={"kind": "sql"},
            body={"sql": SLOW},
            manifest=_signed(issuer, predicates={"nums": "1=1"}),
        )
    )
    assert result.ok is False
    assert result.status == submit.SQL_GATE_ABSTAIN
    assert "limit" in (result.error or "")
    assert not result.output
