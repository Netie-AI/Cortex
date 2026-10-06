"""VERIFIED-QUERY (#309) library on C-MEM solution memory.

Must-fail tests (each has a guard-removed proof in
test_verified_query_guard_mutations.py):

1. ``test_must_fail_unconfirmed_query_never_reused*`` — proposed, non-steward
   confirmed, or real-result-checked only: never matched, never run.
2. ``test_must_fail_scored_pack_never_writes_or_reads_verified_queries`` — the
   C-MEM (#290) scored-pack and scored-round guards cover every write and read.
3. ``test_must_fail_space_b_never_sees_space_a_verified_query`` — Space isolation.
4. ``test_must_fail_revoked_query_never_reused*`` — revoked: never matched or run.
5. ``test_must_fail_param_value_must_be_exactly_its_type`` and
   ``test_must_fail_bound_values_never_enter_sql_text`` — binding cannot inject
   SQL. The grant half (cannot widen) is HTTP-level in
   tests/dms/test_verified_query_contract_ask.py.

No model is called. The model-matcher tests inject a fake matcher.
"""

from __future__ import annotations

import ast
from datetime import date, datetime
from pathlib import Path
from typing import Any

import duckdb
import pytest

from CortexOS.memory import space_memory as sm
from CortexOS.memory import verified_query as vq
from CortexOS.memory.space_memory import (
    NOT_IN_SPACE,
    NOT_STEWARD,
    SCORED_PACK_WRITE,
    SCORED_ROUND_READ,
    SCORED_ROUND_WRITE,
    Actor,
    MemoryRefused,
    SpaceMemory,
)
from CortexOS.memory.verified_query import (
    CONFIRMED,
    PARAM_AMBIGUOUS,
    PARAM_INVALID,
    PARAM_NOT_IN_SQL,
    PENDING,
    REVOKED,
    SQL_INVALID,
    SUPERSEDED,
    ModelPick,
    Param,
    VerifiedQueryError,
    VerifiedQueryLibrary,
)

ROOT = Path(__file__).resolve().parents[2]
STEWARD = Actor("steward-alice", "steward")
VIEWER = Actor("viewer-bob", "viewer")
Q = "What was the total quantity_kg moved for SKU-00296 in June 2026?"
SQL = (
    "SELECT SUM(quantity_kg) AS total_kg, COUNT(*) AS txn_count FROM transactions "
    "WHERE sku = 'SKU-00296' AND timestamp >= '2026-06-01' AND timestamp < '2026-07-01'"
)
ASK = "total quantity_kg moved for sku-00109 in May 2026"
ASK_VALUES = ("SKU-00109", date(2026, 5, 1), date(2026, 6, 1))


@pytest.fixture(autouse=True)
def _unscored(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(sm.SCORED_ROUND_ENV, raising=False)
    monkeypatch.delenv(sm.SCORED_PACKS_ENV, raising=False)


@pytest.fixture
def mem() -> SpaceMemory:
    return SpaceMemory()


@pytest.fixture
def lib(mem: SpaceMemory) -> VerifiedQueryLibrary:
    return VerifiedQueryLibrary(mem)


class Spy:
    def __init__(self, rows: list[dict[str, Any]] | None = None) -> None:
        self.calls: list[tuple[str, tuple[Any, ...]]] = []
        self.rows = rows if rows is not None else [{"total_kg": 12.5, "txn_count": 3}]

    def __call__(self, sql: str, params: Any) -> list[dict[str, Any]]:
        self.calls.append((sql, tuple(params)))
        return [dict(r) for r in self.rows]


def _confirmed(lib: VerifiedQueryLibrary, space: str = "alpha", question: str = Q, sql: str = SQL):
    proposed = lib.propose(space_id=space, question=question, sql=sql, actor="analyst", source="ask:a1")
    return lib.confirm(space_id=space, query_id=proposed.id, steward=STEWARD)


def _reuse(lib: VerifiedQueryLibrary, spy: Spy, space: str = "alpha", question: str = ASK, **kw: Any):
    return lib.reuse(space_id=space, question=question, execute=spy, actor="ask", **kw)


# -- params, intent, binding -------------------------------------------------
def test_propose_discovers_typed_params_from_question_literals(lib: VerifiedQueryLibrary) -> None:
    proposed = lib.propose(space_id="alpha", question=Q, sql=SQL, actor="analyst", source="ask:a1")
    assert proposed.status == PENDING and proposed.entry_id is None
    assert proposed.params == (
        Param("sku_1", "sku", "SKU-00296"),
        Param("date_1", "date", "2026-06-01"),
        Param("date_2", "date", "2026-07-01"),
    )


@pytest.mark.parametrize(
    ("sql", "code"),
    [
        ("DROP TABLE transactions", SQL_INVALID),
        ("SELECT 1; SELECT 2", SQL_INVALID),
        ("SELECT * FROM transactions WHERE sku = $1", SQL_INVALID),
        ("SELECT SUM(quantity_kg) FROM transactions WHERE sku = 'SKU-00296'", PARAM_NOT_IN_SQL),
    ],
)
def test_propose_refuses_sql_that_cannot_be_a_verified_query(
    lib: VerifiedQueryLibrary, sql: str, code: str
) -> None:
    with pytest.raises(MemoryRefused) as ei:
        lib.propose(space_id="alpha", question=Q, sql=sql, actor="analyst", source="ask:a1")
    assert ei.value.code == code
    assert lib.memory.stamps(space_id="alpha")[-1].served_reason == code


def test_propose_refuses_one_value_naming_two_params(lib: VerifiedQueryLibrary) -> None:
    with pytest.raises(MemoryRefused) as ei:
        lib.propose(
            space_id="alpha",
            question="kg moved in June 2026 and July 2026",
            sql="SELECT 1 FROM transactions WHERE timestamp >= '2026-06-01' AND timestamp < '2026-07-01' "
            "OR timestamp >= '2026-07-01' AND timestamp < '2026-08-01'",
            actor="analyst",
            source="ask:a1",
        )
    assert ei.value.code == PARAM_AMBIGUOUS


def test_intent_ignores_values_case_stopwords_and_canonicalises_ontology_terms() -> None:
    terms = {"stock on hand": "quantity_kg"}
    base = vq.intent_signature(Q, terms)
    assert base == ("<date>", "<sku>", "moved", "quantity_kg", "total")
    assert vq.intent_signature("Total stock on hand moved for SKU-1 in 2026-05?", terms) == base
    assert vq.intent_signature("total quantity_kg moved by sku in June 2026", terms) != base


def test_values_extract_in_order_and_a_month_is_two_dates() -> None:
    got = [(v.type, v.value) for v in vq.extract_values("LOC-003 and wh-b for sku-1 from 2026-05-03 to Jun 2026")]
    assert got == [
        ("location_id", "LOC-003"),
        ("warehouse_code", "WH-B"),
        ("sku", "SKU-1"),
        ("date", date(2026, 5, 3)),
        ("date", date(2026, 6, 1)),
        ("date", date(2026, 7, 1)),
    ]


def test_bind_uses_numbered_placeholders_and_renders_display_sql(lib: VerifiedQueryLibrary) -> None:
    query = _confirmed(lib)
    bound = vq.bind(query, {"sku_1": "SKU-00109", "date_1": date(2026, 5, 1), "date_2": date(2026, 6, 1)})
    assert bound.sql == (
        "SELECT SUM(quantity_kg) AS total_kg, COUNT(*) AS txn_count FROM transactions "
        "WHERE sku = $1 AND timestamp >= $2 AND timestamp < $3"
    )
    assert bound.values == ASK_VALUES
    assert "'SKU-00109'" in bound.display_sql and "'2026-05-01'" in bound.display_sql


# -- reuse ----------------------------------------------------------------------
def test_reuse_reruns_bound_sql_every_time_and_is_never_a_validation(lib: VerifiedQueryLibrary) -> None:
    query = _confirmed(lib)
    entry, _ = lib.memory.get(space_id="alpha", entry_id=query.entry_id or "", actor="t")
    assert set(entry.body) == {"question", "sql", "confirmed_by"}, "memory holds no rows or answers"
    assert entry.validation == "steward_confirm"
    assert entry.source == f"verified_query:{query.id};ask:a1"

    spy = Spy()
    first = _reuse(lib, spy)
    spy.rows = [{"total_kg": 99.0, "txn_count": 9}]
    second = _reuse(lib, spy)
    assert first is not None and second is not None
    assert len(spy.calls) == 2, "each ask re-runs the SQL"
    assert first.rows == [{"total_kg": 12.5, "txn_count": 3}]
    assert second.rows == [{"total_kg": 99.0, "txn_count": 9}], "never a cached answer"
    for got in (first, second):
        assert got.reused is True and got.validated is False and got.ok
        assert got.query.id == query.id and got.entry.id == query.entry_id
        assert got.match.matched_by == "deterministic"
        assert got.match.served == vq.no_model_served(query.id)
    assert spy.calls[0][1] == ASK_VALUES
    reuse_stamp = lib.memory.stamps(space_id="alpha")[-1]
    assert reuse_stamp.served_op == "reuse" and reuse_stamp.served_entry_ids == (query.entry_id,)
    assert query.id in reuse_stamp.served_reason


def test_reuse_needs_the_same_intent_and_parameter_shape(lib: VerifiedQueryLibrary) -> None:
    _confirmed(lib)
    spy = Spy()
    assert _reuse(lib, spy, question="total quantity_kg received for SKU-00109 in May 2026") is None
    assert _reuse(lib, spy, question="total quantity_kg moved for SKU-00109") is None
    assert _reuse(lib, spy, question="total quantity_kg moved at LOC-003 in May 2026") is None
    assert _reuse(lib, spy, question="total quantity_kg moved for SKU-1 between 2026-05-01 and 2026-05-09") is None
    assert spy.calls == []


def test_exec_failure_is_stamped_and_not_ok(lib: VerifiedQueryLibrary) -> None:
    _confirmed(lib)

    def boom(sql: str, params: Any) -> list[dict[str, Any]]:
        raise RuntimeError("warehouse down")

    got = lib.reuse(space_id="alpha", question=ASK, execute=boom, actor="ask")
    assert got is not None and not got.ok and got.rows is None
    assert sm.REUSE_EXEC_FAILED in lib.memory.stamps(space_id="alpha")[-1].served_reason


def test_two_live_queries_for_one_question_match_neither(lib: VerifiedQueryLibrary) -> None:
    _confirmed(lib)
    _confirmed(lib, question="Total quantity_kg moved, SKU-00296, June 2026", sql=SQL + " AND 1 = 1")
    spy = Spy()
    assert _reuse(lib, spy) is None
    assert spy.calls == []
    assert lib.memory.stamps(space_id="alpha")[-1].served_reason == vq.AMBIGUOUS_MATCH


def test_reconfirming_a_question_supersedes_the_older_query(lib: VerifiedQueryLibrary) -> None:
    first = _confirmed(lib)
    second = _confirmed(lib, sql=SQL + " AND txn_type = 'OUT'")
    assert second.entry_id == first.entry_id
    by_id = {q.id: q.status for q in lib.list_queries(space_id="alpha", steward=STEWARD)}
    assert by_id == {first.id: SUPERSEDED, second.id: CONFIRMED}
    spy = Spy()
    got = _reuse(lib, spy)
    assert got is not None and got.query.id == second.id
    assert "txn_type = 'OUT'" in spy.calls[0][0]


def test_steward_flows_are_steward_only_and_stamped(lib: VerifiedQueryLibrary) -> None:
    proposed = lib.propose(space_id="alpha", question=Q, sql=SQL, actor="analyst", source="ask:a1")
    for call in (
        lambda: lib.confirm(space_id="alpha", query_id=proposed.id, steward=VIEWER),
        lambda: lib.revoke(space_id="alpha", query_id=proposed.id, steward=VIEWER),
        lambda: lib.list_queries(space_id="alpha", steward=VIEWER),
    ):
        with pytest.raises(MemoryRefused) as ei:
            call()
        assert ei.value.code == NOT_STEWARD
    confirmed = lib.confirm(space_id="alpha", query_id=proposed.id, steward=STEWARD)
    assert confirmed.confirmed_by == STEWARD.actor_id and confirmed.confirmed_at
    with pytest.raises(MemoryRefused) as ei:
        lib.confirm(space_id="alpha", query_id=proposed.id, steward=STEWARD)
    assert ei.value.code == vq.NOT_PENDING
    assert [q.id for q in lib.list_queries(space_id="alpha", steward=STEWARD, status=CONFIRMED)] == [proposed.id]
    ops = [s.served_op for s in lib.memory.stamps(space_id="alpha")]
    assert ops == [
        "vq_propose", "refuse", "refuse", "refuse", "write", "vq_confirm", "refuse", "vq_list",
    ]


# -- must-fail 1: unconfirmed ---------------------------------------------------
def test_must_fail_unconfirmed_query_never_reused(lib: VerifiedQueryLibrary) -> None:
    lib.propose(space_id="alpha", question=Q, sql=SQL, actor="analyst", source="ask:a1")
    spy = Spy()
    assert _reuse(lib, spy) is None
    assert _reuse(lib, spy, question=Q) is None
    assert spy.calls == []
    assert lib.memory.read(space_id="alpha", kind="solution", actor="t")[0] == []


def test_must_fail_unconfirmed_query_never_reused_after_non_steward_confirm(
    lib: VerifiedQueryLibrary,
) -> None:
    proposed = lib.propose(space_id="alpha", question=Q, sql=SQL, actor="analyst", source="ask:a1")
    with pytest.raises(MemoryRefused):
        lib.confirm(space_id="alpha", query_id=proposed.id, steward=VIEWER)
    spy = Spy()
    assert _reuse(lib, spy) is None
    assert spy.calls == []


def test_must_fail_unconfirmed_query_never_reused_on_a_real_result_check(
    lib: VerifiedQueryLibrary,
) -> None:
    """A real-result-checked C-MEM solution is not a steward confirm of the query."""
    lib.propose(space_id="alpha", question=Q, sql=SQL, actor="analyst", source="ask:a1")
    lib.memory.check_solution(
        space_id="alpha",
        question=Q,
        sql=SQL,
        expected_rows=[{"total_kg": 1.0, "txn_count": 1}],
        execute=lambda sql: [{"total_kg": 1.0, "txn_count": 1}],
        source="ask:a1",
        actor="analyst",
    )
    spy = Spy()
    assert _reuse(lib, spy) is None
    assert spy.calls == []


# -- must-fail 2: scored packs --------------------------------------------------
def test_must_fail_scored_pack_never_writes_or_reads_verified_queries(
    lib: VerifiedQueryLibrary, monkeypatch: pytest.MonkeyPatch
) -> None:
    for kwargs in (
        {"pack_id": "curated_ceo", "source": "ask:a1"},
        {"source": "bird_minidev:q12"},
        {"source": "ask:a1", "scored_pack_id": "pack_d"},
    ):
        with pytest.raises(MemoryRefused) as ei:
            lib.propose(space_id="alpha", question=Q, sql=SQL, actor="analyst", **kwargs)
        assert ei.value.code == SCORED_PACK_WRITE, kwargs

    proposed = lib.propose(space_id="alpha", question=Q, sql=SQL, actor="analyst", source="ask:a1")
    with pytest.raises(MemoryRefused) as ei:
        lib.confirm(space_id="alpha", query_id=proposed.id, steward=STEWARD, scored_pack_id="curated_ceo")
    assert ei.value.code == SCORED_PACK_WRITE
    lib.confirm(space_id="alpha", query_id=proposed.id, steward=STEWARD)

    spy = Spy()
    assert _reuse(lib, spy, scored_pack_id="curated_ceo") is None
    assert lib.memory.stamps(space_id="alpha")[-1].served_reason == SCORED_ROUND_READ
    monkeypatch.setenv(sm.SCORED_ROUND_ENV, "1")
    assert _reuse(lib, spy) is None
    with pytest.raises(MemoryRefused) as ei:
        lib.propose(space_id="alpha", question=Q, sql=SQL, actor="analyst", source="ask:a2")
    assert ei.value.code == SCORED_ROUND_WRITE
    assert spy.calls == []


# -- must-fail 3: Space isolation ----------------------------------------------
def test_must_fail_space_b_never_sees_space_a_verified_query(lib: VerifiedQueryLibrary) -> None:
    query = _confirmed(lib, space="alpha")
    pending = lib.propose(space_id="alpha", question="kg moved at LOC-003", sql="SELECT 1 FROM transactions WHERE location_id = 'LOC-003'", actor="analyst", source="ask:a2")
    spy = Spy()
    assert _reuse(lib, spy, space="beta") is None
    assert _reuse(lib, spy, space="beta", question=Q) is None
    assert spy.calls == []
    assert lib.list_queries(space_id="beta", steward=STEWARD) == []
    for call in (
        lambda: lib.confirm(space_id="beta", query_id=pending.id, steward=STEWARD),
        lambda: lib.revoke(space_id="beta", query_id=query.id, steward=STEWARD),
    ):
        with pytest.raises(MemoryRefused) as ei:
            call()
        assert ei.value.code == NOT_IN_SPACE
    assert _reuse(lib, spy, space="alpha") is not None, "Space A still has its own query"


# -- must-fail 4: revoked ---------------------------------------------------------
def test_must_fail_revoked_query_never_reused(lib: VerifiedQueryLibrary) -> None:
    query = _confirmed(lib)
    spy = Spy()
    assert _reuse(lib, spy) is not None
    revoked = lib.revoke(space_id="alpha", query_id=query.id, steward=STEWARD)
    assert revoked.status == REVOKED and revoked.revoked_by == STEWARD.actor_id
    assert _reuse(lib, spy) is None
    assert _reuse(lib, spy, question=Q) is None
    assert len(spy.calls) == 1, "a revoked query is never run again"
    entries, _ = lib.memory.steward_view(space_id="alpha", steward=STEWARD, kind="solution")
    assert entries == [], "revoke removes the solution from memory"


def test_must_fail_revoked_query_never_reused_after_plain_solution_confirm(
    lib: VerifiedQueryLibrary,
) -> None:
    """A later plain C-MEM confirm of the same question does not revive the query."""
    query = _confirmed(lib)
    lib.revoke(space_id="alpha", query_id=query.id, steward=STEWARD)
    lib.memory.confirm_solution(space_id="alpha", question=Q, sql=SQL, steward=STEWARD, source="ask:a9")
    spy = Spy()
    assert _reuse(lib, spy) is None
    assert spy.calls == []


# -- must-fail 5: binding --------------------------------------------------------
HOSTILE = [
    ("sku_1", "SKU-00296' OR '1'='1"),
    ("sku_1", "SKU-1'; DROP TABLE transactions; --"),
    ("sku_1", "sku-00296"),
    ("sku_1", 296),
    ("date_1", "2026-05-01' OR '1'='1"),
    ("date_1", "2026-05-01"),
    ("date_1", datetime(2026, 5, 1)),
]


@pytest.mark.parametrize(("name", "value"), HOSTILE, ids=[repr(v) for _, v in HOSTILE])
def test_must_fail_param_value_must_be_exactly_its_type(
    lib: VerifiedQueryLibrary, name: str, value: Any
) -> None:
    query = _confirmed(lib)
    good: dict[str, Any] = {"sku_1": "SKU-00109", "date_1": date(2026, 5, 1), "date_2": date(2026, 6, 1)}
    with pytest.raises(VerifiedQueryError) as ei:
        vq.bind(query, {**good, name: value})
    assert ei.value.code == PARAM_INVALID


@pytest.mark.parametrize(
    "value",
    ["SKU-1' OR '1'='1", "x') OR (1=1", "SKU-1'; DELETE FROM t; --"],
)
def test_must_fail_bound_values_never_enter_sql_text(value: str) -> None:
    params = (Param("sku_1", "sku", "SKU-00296"),)
    sql, values = vq.bind_sql("SELECT COUNT(*) AS n FROM t WHERE sku = 'SKU-00296'", params, [value])
    assert sql == "SELECT COUNT(*) AS n FROM t WHERE sku = $1"
    assert values == (value,)
    con = duckdb.connect()
    con.execute("CREATE TABLE t (sku VARCHAR)")
    con.execute("INSERT INTO t VALUES ('SKU-00296'), ('SKU-00109'), ('SKU-1')")
    assert con.execute(sql, list(values)).fetchone() == (0,), "a bound value is data, not SQL"
    assert con.execute("SELECT COUNT(*) FROM t").fetchone() == (3,)


def test_bind_never_changes_tables_or_columns(lib: VerifiedQueryLibrary) -> None:
    import sqlglot
    from sqlglot import exp

    query = _confirmed(lib)
    bound = vq.bind(query, dict(zip(("sku_1", "date_1", "date_2"), ASK_VALUES, strict=True)))

    def shape(sql: str) -> tuple[set[str], set[str]]:
        tree = sqlglot.parse_one(sql, read="duckdb")
        return {t.name for t in tree.find_all(exp.Table)}, {c.name for c in tree.find_all(exp.Column)}

    assert shape(bound.sql) == shape(SQL) == shape(bound.display_sql)


# -- model-assisted matching ----------------------------------------------------
RECORDED_SERVED = {
    "served_provider": "groq",
    "served_model": "openai/gpt-oss-120b",
    "served_local": False,
    "served_reason": "",
}


def test_model_matcher_runs_only_after_a_deterministic_miss_and_picks_a_live_id(
    lib: VerifiedQueryLibrary,
) -> None:
    query = _confirmed(lib)
    seen: list[list[str]] = []

    def model(question: str, candidates: Any) -> ModelPick:
        seen.append([c.id for c in candidates])
        return ModelPick(query.id, RECORDED_SERVED)

    spy = Spy()
    assert _reuse(lib, spy, model=model) is not None
    assert seen == [], "deterministic hit: no model call"
    got = _reuse(lib, spy, question="how many kilos of SKU-00109 shifted during May 2026", model=model)
    assert seen == [[query.id]]
    assert got is not None and got.match.matched_by == "model"
    assert got.match.served == RECORDED_SERVED
    assert spy.calls[-1][1] == ASK_VALUES, "values stay deterministic"
    assert "served_model=openai/gpt-oss-120b" in lib.memory.stamps(space_id="alpha")[-1].served_reason


def test_model_matcher_cannot_pick_outside_live_shape_fitting_queries(lib: VerifiedQueryLibrary) -> None:
    query = _confirmed(lib)
    pending = lib.propose(space_id="alpha", question="kg at LOC-003", sql="SELECT 1 FROM transactions WHERE location_id = 'LOC-003'", actor="analyst", source="ask:a2")
    spy = Spy()
    for pick in (pending.id, "vq_made_up", "DROP TABLE transactions", None):
        assert _reuse(lib, spy, question="kilos of SKU-00109 in May 2026", model=lambda q, c, p=pick: ModelPick(p)) is None
    calls: list[int] = []
    assert _reuse(lib, spy, question="kilos moved, no values", model=lambda q, c: calls.append(1) or ModelPick(query.id)) is None
    assert calls == [], "no shape-fitting candidate: model not asked"
    assert spy.calls == []


def test_model_pick_without_route_stamp_never_claims_no_model(lib: VerifiedQueryLibrary) -> None:
    query = _confirmed(lib)
    got = _reuse(lib, Spy(), question="kilos of SKU-00109 in May 2026", model=lambda q, c: ModelPick(query.id))
    assert got is not None
    assert "no model called" not in str(got.match.served["served_reason"])


# -- module boundary (AST: function-level imports too) -------------------------
FORBIDDEN = (
    "packs", "duckdb", "litellm", "httpx", "CortexOS.integrations", "CortexOS.crew",
    "CortexOS.nlp", "CortexOS.execution", "CortexOS.dms", "CortexOS.api",
)


def _forbidden_imports(source: str) -> list[str]:
    hits: list[str] = []
    for node in ast.walk(ast.parse(source)):
        names = (
            [a.name for a in node.names] if isinstance(node, ast.Import)
            else [node.module or ""] if isinstance(node, ast.ImportFrom) else []
        )
        hits += [n for n in names if any(n == f or n.startswith(f + ".") for f in FORBIDDEN)]
    return hits


def test_library_never_imports_a_model_pack_db_driver_or_answer_plane() -> None:
    source = (ROOT / "CortexOS" / "memory" / "verified_query.py").read_text(encoding="utf-8")
    assert _forbidden_imports(source) == []


@pytest.mark.parametrize("line", ["import duckdb", "from packs.dms import x", "from CortexOS.integrations import freeroute"])
def test_must_fail_library_boundary_check_catches_function_level_imports(line: str) -> None:
    assert _forbidden_imports(f"def f():\n    {line}\n") != []
