"""C-LOOP (#291) analysis loop runner, driven through stub ports.

Must-fails (each also fails with its guard removed — see
test_c_loop_guard_mutations.py):

* ``test_must_fail_wrong_answer_caught_by_evaluate`` — a wrong total that a
  steward formula disagrees with becomes a named abstain, never an answer.
* ``test_must_fail_wrong_answer_caught_by_check`` — SQL over an unmapped table,
  an empty, all-NULL or non-finite result becomes a named abstain.
* ``test_must_fail_abstain_without_named_reason`` — an abstain that does not
  name a ``LoopAbstainReason`` cannot be built or emitted.
* ``test_must_fail_scored_round_loop_writes_no_memory`` — the C-MEM guard
  refuses the loop's memory write on a scored pack.

No model is called. The extract is an in-memory sqlite stub; the proposer is a
stub, or replays the recorded C-STAMP OpenVault response
(tests/dms/fixtures/c_stamp_289).
"""

from __future__ import annotations

import json
import math
import sqlite3
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from cortex_contract.answer import AnalysisLoop, LoopAbstainReason
from pydantic import ValidationError

from CortexOS.loop import runner as loop_runner
from CortexOS.loop.runner import Candidate, LoopPorts, run_analysis_loop
from CortexOS.memory import space_memory as sm
from CortexOS.memory.space_memory import Actor, SpaceMemory

C_STAMP = Path(__file__).resolve().parents[1] / "dms" / "fixtures" / "c_stamp_289"
STEWARD = Actor("steward-alice", "steward")
REVENUE_Q = "what is our total revenue"
RIGHT_SQL = (
    "SELECT ROUND(SUM(quantity_kg * unit_cost_myr), 2) AS revenue_myr "
    "FROM transactions WHERE txn_type = 'OUT'"
)
# A typical analyst slip: inbound stock counted as revenue.
WRONG_SQL = "SELECT ROUND(SUM(quantity_kg * unit_cost_myr), 2) AS revenue_myr FROM transactions"
SCHEMA = {
    "tables": {
        "transactions": {"columns": ["txn_type", "sku", "quantity_kg", "unit_cost_myr"]},
        "inventory": {"columns": ["sku", "qty"]},
        "suppliers": {"columns": ["supplier_id", "supplier_name"]},
    },
    "joins": [
        {"from_table": "transactions", "from_column": "sku", "to_table": "inventory", "to_column": "sku"},
        {"from_table": "inventory", "from_column": "sku", "to_table": "suppliers", "to_column": "sku"},
    ],
}
GRANTED = ("inventory", "transactions")


@pytest.fixture(autouse=True)
def _unscored(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(sm.SCORED_ROUND_ENV, raising=False)
    monkeypatch.delenv(sm.SCORED_PACKS_ENV, raising=False)


class Extract:
    """Session extract stub: sqlite rows, every statement recorded."""

    def __init__(self) -> None:
        self.con = sqlite3.connect(":memory:")
        self.con.row_factory = sqlite3.Row
        self.con.executescript(
            """
            CREATE TABLE transactions (txn_type TEXT, sku TEXT, quantity_kg REAL, unit_cost_myr REAL);
            INSERT INTO transactions VALUES ('OUT','A',10,2.5),('OUT','B',4,10),('IN','A',100,2.5);
            CREATE TABLE inventory (sku TEXT, qty INTEGER);
            INSERT INTO inventory VALUES ('A',5),('B',7),('C',0),('D',3),('E',9),('F',1);
            CREATE TABLE suppliers (supplier_id TEXT, supplier_name TEXT);
            """
        )
        self.calls: list[str] = []

    def __call__(self, sql: str) -> list[dict[str, Any]]:
        self.calls.append(sql)
        return [dict(r) for r in self.con.execute(sql).fetchall()]


@pytest.fixture
def extract() -> Extract:
    return Extract()


@pytest.fixture
def mem() -> SpaceMemory:
    return SpaceMemory()


def _engine(extract: Extract, sql: str | None, **kw: Any) -> Callable[[str], Candidate]:
    def propose(question: str) -> Candidate:
        rows = kw.pop("rows", None)
        if rows is None and sql:
            rows = extract(sql)
        return Candidate(sql=sql, rows=rows or [], served_by="engine:governed_metric", **kw)

    return propose


def _ports(extract: Extract, propose: Callable[[str], Candidate], **kw: Any) -> LoopPorts:
    base: dict[str, Any] = {
        "space_id": "alpha",
        "granted": GRANTED,
        "schema": SCHEMA,
        "execute": extract,
        "propose": propose,
    }
    base.update(kw)
    return LoopPorts(**base)


def _run(ports: LoopPorts, question: str = REVENUE_Q, **kw: Any):
    return run_analysis_loop(question, ports=ports, audit_id="audit-1", **kw)


def _steps(result) -> list[tuple[str, str]]:
    return [(s.served_step, s.served_status) for s in result.steps]


def _revenue_formula(mem: SpaceMemory, space: str = "alpha") -> str:
    entry, _ = mem.write(
        space_id=space,
        kind="formula",
        key="revenue_total",
        body={"metric": "total revenue", "phrases": ["total revenue"], "sql": RIGHT_SQL},
        source="steward:alice",
        actor=STEWARD.actor_id,
    )
    return entry.id


# -- stamps -------------------------------------------------------------------
def test_every_step_is_stamped_in_run_order(extract: Extract, mem: SpaceMemory) -> None:
    formula_id = _revenue_formula(mem)
    result = _run(_ports(extract, _engine(extract, RIGHT_SQL), memory=mem))
    assert result.outcome == "answer" and result.abstain_reason is None
    assert _steps(result) == [
        ("plan", "ok"),
        ("memory_lookup", "ok"),
        ("ontology_lookup", "ok"),
        ("sql", "ok"),
        ("check", "ok"),
        ("evaluate", "ok"),
        ("memory_write", "ok"),
        ("answer", "ok"),
    ]
    assert [s.seq for s in result.steps] == list(range(1, 9))
    for step in result.steps:
        assert step.served_by and step.served_at
    by = {s.served_step: s for s in result.steps}
    assert by["sql"].served_by == "engine:governed_metric"
    assert by["sql"].served_sql == [RIGHT_SQL] and by["sql"].served_row_count == 1
    assert by["ontology_lookup"].served_tool == "data_map"
    assert by["evaluate"].served_memory_ids == [formula_id]
    assert by["evaluate"].served_sql == [RIGHT_SQL]
    assert result.sql_run == [RIGHT_SQL, RIGHT_SQL]
    assert formula_id in result.memory_ids_read
    assert result.candidate is not None and result.candidate.rows == [{"revenue_myr": 65.0}]
    record = result.record()
    assert isinstance(record, AnalysisLoop) and record.outcome == "answer"


def test_loop_without_memory_still_plans_checks_and_answers(extract: Extract) -> None:
    result = _run(_ports(extract, _engine(extract, RIGHT_SQL)))
    assert result.outcome == "answer"
    by = {s.served_step: s for s in result.steps}
    assert by["memory_lookup"].served_status == "skipped"
    assert by["memory_lookup"].served_reason == "per-Space memory is off"
    assert by["evaluate"].served_status == "skipped"
    assert "memory_write" not in by
    assert result.memory_ids_read == []


# -- must-fail: wrong answers are caught -------------------------------------
def test_must_fail_wrong_answer_caught_by_evaluate(extract: Extract, mem: SpaceMemory) -> None:
    formula_id = _revenue_formula(mem)
    wrong = extract(WRONG_SQL)
    assert wrong != extract(RIGHT_SQL)  # the stub really is wrong
    result = _run(_ports(extract, _engine(extract, WRONG_SQL), memory=mem))
    assert result.outcome == "abstain", "a wrong total was served"
    assert result.abstain_reason is LoopAbstainReason.FORMULA_MISMATCH
    by = {s.served_step: s for s in result.steps}
    assert by["evaluate"].served_status == "abstain"
    assert by["evaluate"].served_memory_ids == [formula_id]
    assert by["sql"].served_sql == [WRONG_SQL]
    assert result.steps[-1].served_step == "abstain"
    assert result.steps[-1].served_reason.startswith("formula_mismatch: ")
    assert "answer" not in by


@pytest.mark.parametrize(
    ("sql", "rows", "reason"),
    [
        ("SELECT supplier_name FROM suppliers", [{"supplier_name": "x"}], LoopAbstainReason.UNGRANTED_TABLE),
        ("SELECT sku FROM inventory WHERE qty > 100", None, LoopAbstainReason.EMPTY_RESULT),
        ("SELECT SUM(qty) AS total FROM inventory WHERE qty > 100", None, LoopAbstainReason.NULL_RESULT),
        ("SELECT qty FROM inventory", [{"qty": math.inf}], LoopAbstainReason.NON_FINITE_VALUE),
        ("SELEC qty FRM", [{"qty": 1}], LoopAbstainReason.SQL_NOT_ANALYSABLE),
    ],
    ids=["ungranted_table", "empty_result", "null_result", "non_finite", "unanalysable"],
)
def test_must_fail_wrong_answer_caught_by_check(
    extract: Extract, sql: str, rows: list[dict[str, Any]] | None, reason: LoopAbstainReason
) -> None:
    result = _run(_ports(extract, _engine(extract, sql, rows=rows)))
    assert result.outcome == "abstain", f"{reason.value} was served as an answer"
    assert result.abstain_reason is reason
    by = {s.served_step: s for s in result.steps}
    assert by["check"].served_status == "abstain"
    assert "evaluate" not in by and "answer" not in by


# -- must-fail: an abstain always names its reason ----------------------------
def test_must_fail_abstain_without_named_reason() -> None:
    with pytest.raises(ValidationError, match="abstain_reason"):
        AnalysisLoop(outcome="abstain")
    with pytest.raises(ValidationError):
        AnalysisLoop(outcome="abstain", abstain_reason="something went wrong")
    with pytest.raises(ValidationError, match="abstain_reason"):
        AnalysisLoop(outcome="answer", abstain_reason=LoopAbstainReason.EMPTY_RESULT)
    with pytest.raises(TypeError, match="LoopAbstainReason"):
        loop_runner._Abstain("no_trustworthy_path", "a bare string is not a named reason")  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("ports_kw", "propose_kw", "reason"),
    [
        ({"grant_error": "no session grant is bound"}, {}, LoopAbstainReason.UNGROUNDED_SESSION),
        ({"blocked": True}, {}, LoopAbstainReason.POLICY_BLOCKED),
        ({}, {"abstain": LoopAbstainReason.NO_TRUSTWORTHY_PATH, "reason": "no metric"}, LoopAbstainReason.NO_TRUSTWORTHY_PATH),
        ({}, {"abstain": LoopAbstainReason.ENGINE_REFUSED, "reason": "manifest"}, LoopAbstainReason.ENGINE_REFUSED),
        ({}, {"sql": None, "reason": "catalog"}, LoopAbstainReason.NOT_A_SQL_ANSWER),
    ],
    ids=["ungrounded", "blocked", "no_path", "engine_refused", "not_sql"],
)
def test_every_loop_abstain_names_its_reason(
    extract: Extract, ports_kw: dict[str, Any], propose_kw: dict[str, Any], reason: LoopAbstainReason
) -> None:
    sql = propose_kw.pop("sql", RIGHT_SQL)
    result = _run(_ports(extract, _engine(extract, sql, **propose_kw), **ports_kw))
    assert result.outcome == "abstain"
    assert result.abstain_reason is reason
    last = result.steps[-1]
    assert (last.served_step, last.served_status) == ("abstain", "abstain")
    assert last.served_reason.startswith(f"{reason.value}: ") and len(last.served_reason) > len(reason.value) + 2
    assert result.record().abstain_reason is reason


# -- memory: four kinds, Space isolation, no scored writes ---------------------
def test_must_fail_scored_round_loop_writes_no_memory(extract: Extract, mem: SpaceMemory) -> None:
    mem.confirm_solution(
        space_id="alpha", question=REVENUE_Q, sql=RIGHT_SQL, steward=STEWARD, source="steward:alice"
    )
    result = _run(
        _ports(extract, _engine(extract, RIGHT_SQL), memory=mem), scored_pack_id="curated_ceo"
    )
    by = {s.served_step: s for s in result.steps}
    assert by["memory_write"].served_status == "refused", "the loop wrote memory on a scored pack"
    assert by["memory_write"].served_reason == sm.SCORED_PACK_WRITE
    assert result.reused is False and by["sql"].served_by == "engine:governed_metric"
    assert mem.read(space_id="alpha", kind="tool", actor="t")[0] == []
    assert "scored_round=True" in by["plan"].served_reason


def test_solution_reuse_reruns_sql_on_current_data(extract: Extract, mem: SpaceMemory) -> None:
    entry, _ = mem.confirm_solution(
        space_id="alpha", question=REVENUE_Q, sql=RIGHT_SQL, steward=STEWARD, source="steward:alice"
    )
    engine_called: list[str] = []

    def propose(q: str) -> Candidate:
        engine_called.append(q)
        return Candidate(sql=RIGHT_SQL, rows=extract(RIGHT_SQL), served_by="engine:x")

    first = _run(_ports(extract, propose, memory=mem))
    assert first.reused and first.candidate is not None
    assert first.candidate.rows == [{"revenue_myr": 65.0}]
    extract.con.execute("INSERT INTO transactions VALUES ('OUT','C',1,5)")
    before = len(extract.calls)
    second = _run(_ports(extract, propose, memory=mem))
    assert extract.calls[before] == RIGHT_SQL, "reuse must re-run the SQL"
    assert second.candidate is not None and second.candidate.rows == [{"revenue_myr": 70.0}]
    assert engine_called == []
    sql_step = next(s for s in second.steps if s.served_step == "sql")
    assert sql_step.served_by == f"memory:{entry.id}@v1"
    assert sql_step.served_memory_ids == [entry.id]
    assert "not a validation" in sql_step.served_reason
    assert entry.id in second.memory_ids_read


def test_reused_solution_is_still_checked_and_evaluated(extract: Extract, mem: SpaceMemory) -> None:
    _revenue_formula(mem)
    mem.confirm_solution(
        space_id="alpha", question=REVENUE_Q, sql=WRONG_SQL, steward=STEWARD, source="steward:alice"
    )
    result = _run(_ports(extract, _engine(extract, RIGHT_SQL), memory=mem))
    assert result.reused is True
    assert result.outcome == "abstain" and result.abstain_reason is LoopAbstainReason.FORMULA_MISMATCH


def test_table_formula_and_tool_memory_are_consumed(extract: Extract, mem: SpaceMemory) -> None:
    table, _ = mem.write(
        space_id="alpha",
        kind="table",
        key="inventory",
        body={"table": "inventory", "keys": ["sku"], "row_count": 6, "columns": ["sku", "qty", "bin"]},
        source="profile:alpha",
        actor="cortex:profiler",
    )
    formula_id = _revenue_formula(mem)
    first = _run(_ports(extract, _engine(extract, RIGHT_SQL), memory=mem), question="chart total revenue")
    data_map = next(t for t in first.tools if t.tool == "data_map").output
    assert data_map is not None
    node = next(n for n in data_map["nodes"] if n["table"] == "inventory")
    assert node["row_count"] == 6 and node["keys"] == ["sku"] and "bin" in node["columns"]
    assert node["memory_ids"] == [table.id]
    assert {e["from"] for e in data_map["edges"]} == {"transactions"}  # suppliers not granted
    assert {table.id, formula_id} <= set(first.memory_ids_read)
    assert any(t.tool == "chart_spec" and t.served_status == "ok" for t in first.tools)

    written = next(s for s in first.steps if s.served_step == "memory_write").served_memory_ids
    second = _run(_ports(extract, _engine(extract, RIGHT_SQL), memory=mem), question="chart total revenue")
    lookup = next(s for s in second.steps if s.served_step == "memory_lookup")
    assert written[0] in lookup.served_memory_ids
    tool_entry = mem.get(space_id="alpha", entry_id=written[0], actor="t")[0]
    assert tool_entry.body["tools"] == {"data_map": "ok", "chart_spec": "ok"}
    assert tool_entry.body["outcome"] == "answer" and tool_entry.version == 2


def test_tool_memory_plans_the_chart_for_a_question_it_solved(extract: Extract, mem: SpaceMemory) -> None:
    mem.write(
        space_id="alpha",
        kind="tool",
        key=f"loop:{sm.question_key(REVENUE_Q)}",
        body={"tools": {"chart_spec": "ok"}},
        source="loop:earlier",
        actor=loop_runner.LOOP_ACTOR,
    )
    result = _run(_ports(extract, _engine(extract, RIGHT_SQL), memory=mem))
    lookup = next(s for s in result.steps if s.served_step == "memory_lookup")
    assert "chart_spec planned from tool memory" in lookup.served_reason
    chart = next(t for t in result.tools if t.tool == "chart_spec")
    assert chart.output == {
        "type": "bignum",
        "value": 65.0,
        "label": "REVENUE MYR",
        "title": REVENUE_Q,
        "data": [],
    }


@pytest.mark.parametrize("kind", ["table", "formula", "tool", "solution"])
def test_space_b_loop_never_reads_space_a_memory(extract: Extract, mem: SpaceMemory, kind: str) -> None:
    if kind == "solution":
        entry, _ = mem.confirm_solution(
            space_id="alpha", question=REVENUE_Q, sql=WRONG_SQL, steward=STEWARD, source="s"
        )
    elif kind == "formula":
        entry = mem.get(space_id="alpha", entry_id=_revenue_formula(mem), actor="t")[0]
    else:
        key = "transactions" if kind == "table" else f"loop:{sm.question_key(REVENUE_Q)}"
        entry, _ = mem.write(
            space_id="alpha", kind=kind, key=key, body={"table": "transactions"}, source="s", actor="t"
        )
    result = _run(_ports(extract, _engine(extract, RIGHT_SQL), memory=mem, space_id="beta"))
    assert entry.id not in result.memory_ids_read
    assert all(entry.id not in s.served_memory_ids for s in result.steps)
    assert result.outcome == "answer" and result.reused is False


# -- tools via the recorded transcript ----------------------------------------
def test_chart_and_data_map_on_recorded_c_stamp_transcript(extract: Extract) -> None:
    body = json.loads((C_STAMP / "resp_body.json").read_text(encoding="utf-8"))
    recorded_sql = body["choices"][0]["message"]["content"].strip().rstrip(";")
    served = f"engine:generated ({body['served_provider']}/{body['served_model']}, recorded)"

    def replay(q: str) -> Candidate:
        return Candidate(sql=recorded_sql, rows=extract(recorded_sql), served_by=served)

    result = _run(_ports(extract, replay), question="plot some skus from inventory")
    assert result.outcome == "answer"
    sql_step = next(s for s in result.steps if s.served_step == "sql")
    assert sql_step.served_by == served and sql_step.served_sql == [recorded_sql]
    tools = {t.tool: t for t in result.tools}
    assert set(tools) == {"data_map", "chart_spec"}
    assert {n["table"] for n in tools["data_map"].output["nodes"]} == set(GRANTED)
    assert tools["chart_spec"].output["type"] == "none"
    assert tools["chart_spec"].output["reason"] == "no numeric column"
