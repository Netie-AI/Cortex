"""C-LOOP (#291) analysis-loop orchestrator, driven through stub ports.

Generator, self-correct and packager are injected stubs (their real versions are
C-LOOP-A #303, -B #304 and -C #305). Must-fails (each also fails with its guard
removed — see test_c_loop_guard_mutations.py):

* ``test_must_fail_wrong_answer_caught_by_evaluate`` — a wrong total that a
  steward formula disagrees with becomes a named abstain, never an answer.
* ``test_must_fail_wrong_answer_caught_by_check`` — SQL over an unmapped table,
  an empty, all-NULL or non-finite result becomes a named abstain.
* ``test_must_fail_abstain_without_named_reason`` — an abstain that does not
  name a snake_case reason code cannot be built or emitted.
* ``test_must_fail_scored_round_loop_writes_no_memory`` — the C-MEM guard
  refuses the loop's memory write on a scored pack.

No model is called. The extract is an in-memory sqlite stub; the generator is a
stub, or replays the recorded C-STAMP OpenVault response
(tests/dms/fixtures/c_stamp_289).
"""

from __future__ import annotations

import json
import math
import sqlite3
from pathlib import Path
from typing import Any

import pytest
from cortex_contract.answer import AnalysisLoop, LoopStep
from pydantic import ValidationError

from CortexOS.loop import orchestrator as loop_orchestrator
from CortexOS.loop.interfaces import (
    Abstained,
    Candidate,
    Checked,
    Failure,
    LoopReason,
    PlanContext,
)
from CortexOS.loop.orchestrator import LoopPorts, LoopResult, run_analysis_loop
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
        self.overrides: dict[str, list[dict[str, Any]]] = {}

    def __call__(self, sql: str) -> list[dict[str, Any]]:
        self.calls.append(sql)
        if sql in self.overrides:
            return self.overrides[sql]
        return [dict(r) for r in self.con.execute(sql).fetchall()]


class Generator:
    """Stub generator: one SQL per call (cycling), every plan context recorded."""

    served_by = "stub-generator"

    def __init__(self, *sqls: str | None, **kw: Any) -> None:
        self.sqls = list(sqls) or [RIGHT_SQL]
        self.kw = kw
        self.contexts: list[PlanContext] = []

    def __call__(self, context: PlanContext) -> Candidate:
        self.contexts.append(context)
        sql = self.sqls[min(len(self.contexts), len(self.sqls)) - 1]
        return Candidate(sql=sql, served_by="engine:governed_metric", **self.kw)


class Packager:
    """Stub packager: records what it was handed, returns a marker envelope."""

    served_by = "stub-packager"

    def __init__(self) -> None:
        self.calls: list[Checked] = []

    def __call__(self, context: PlanContext, checked: Checked) -> dict[str, Any]:
        self.calls.append(checked)
        return {"packaged": True, "rows": checked.rows, "sql_used": checked.sql}


@pytest.fixture
def extract() -> Extract:
    return Extract()


@pytest.fixture
def mem() -> SpaceMemory:
    return SpaceMemory()


@pytest.fixture
def packager() -> Packager:
    return Packager()


def _ports(extract: Extract, generate: Any = None, **kw: Any) -> LoopPorts:
    base: dict[str, Any] = {
        "space_id": "alpha",
        "granted": GRANTED,
        "schema": SCHEMA,
        "execute": extract,
        "generate": generate or Generator(),
        "package": kw.pop("package", None) or Packager(),
    }
    base.update(kw)
    return LoopPorts(**base)


def _run(ports: LoopPorts, question: str = REVENUE_Q, **kw: Any) -> LoopResult:
    return run_analysis_loop(question, ports=ports, audit_id="audit-1", **kw)


def _steps(result: LoopResult) -> list[tuple[str, str]]:
    return [(s.served_step, s.served_status) for s in result.steps]


def _by(result: LoopResult) -> dict[str, LoopStep]:
    return {s.served_step: s for s in result.steps}


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
def test_every_step_is_stamped_in_run_order(extract: Extract, mem: SpaceMemory, packager: Packager) -> None:
    formula_id = _revenue_formula(mem)
    result = _run(_ports(extract, memory=mem, package=packager))
    assert result.outcome == "answer" and result.reason is None
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
    by = _by(result)
    assert by["sql"].served_by == "engine:governed_metric"
    assert by["sql"].served_sql == [RIGHT_SQL] and by["sql"].served_row_count == 1
    assert by["ontology_lookup"].served_tool == "data_map"
    assert by["evaluate"].served_memory_ids == [formula_id]
    assert by["evaluate"].served_sql == [RIGHT_SQL]
    assert by["answer"].served_by == "packager:stub-packager"
    assert formula_id in result.memory_ids_read
    assert result.envelope == {"packaged": True, "rows": [{"revenue_myr": 65.0}], "sql_used": RIGHT_SQL}
    assert [c.rows for c in packager.calls] == [[{"revenue_myr": 65.0}]]
    record = result.record()
    assert isinstance(record, AnalysisLoop) and record.outcome == "answer"


def test_loop_without_memory_still_plans_checks_and_answers(extract: Extract) -> None:
    result = _run(_ports(extract))
    assert result.outcome == "answer"
    by = _by(result)
    assert by["memory_lookup"].served_status == "skipped"
    assert by["memory_lookup"].served_reason == "per-Space memory is off"
    assert by["evaluate"].served_status == "skipped"
    assert "memory_write" not in by
    assert result.memory_ids_read == []


# -- injected seams --------------------------------------------------------------
def test_generator_gets_the_plan_context_from_grant_and_memory(extract: Extract, mem: SpaceMemory) -> None:
    table, _ = mem.write(
        space_id="alpha",
        kind="table",
        key="inventory",
        body={"table": "inventory", "keys": ["sku"], "row_count": 6},
        source="profile:alpha",
        actor="cortex:profiler",
    )
    formula_id = _revenue_formula(mem)
    gen = Generator()
    _run(_ports(extract, gen, memory=mem))
    (ctx,) = gen.contexts
    assert ctx.question == REVENUE_Q and ctx.space_id == "alpha" and ctx.granted == GRANTED
    assert {n["table"] for n in ctx.data_map["nodes"]} == set(GRANTED)
    assert [t["id"] for t in ctx.table_memory] == [table.id]
    assert [f["id"] for f in ctx.formula_memory] == [formula_id]


def test_generator_sql_always_runs_through_the_injected_executor(extract: Extract) -> None:
    def refuse(sql: str) -> list[dict[str, Any]]:
        extract.calls.append(sql)
        raise PermissionError("SQL failed the gate")

    packager = Packager()
    result = _run(_ports(extract, execute=refuse, package=packager))
    assert extract.calls == [RIGHT_SQL]
    assert result.outcome == "abstain" and result.reason == LoopReason.SQL_FAILED.value
    by = _by(result)
    assert by["sql"].served_status == "failed" and "SQL failed the gate" in by["sql"].served_reason
    assert "check" not in by and packager.calls == []


def test_self_correct_candidate_is_rerun_checked_and_evaluated(extract: Extract, mem: SpaceMemory) -> None:
    formula_id = _revenue_formula(mem)
    seen: list[Failure] = []

    def fix(context: PlanContext, candidate: Candidate, failure: Failure) -> Candidate:
        seen.append(failure)
        return Candidate(sql=RIGHT_SQL, served_by="stub-correct")

    result = _run(_ports(extract, Generator(WRONG_SQL), memory=mem, self_correct=fix))
    assert result.outcome == "answer"
    assert [(f.step, f.reason, f.sql) for f in seen] == [("evaluate", "formula_mismatch", WRONG_SQL)]
    assert _steps(result) == [
        ("plan", "ok"),
        ("memory_lookup", "ok"),
        ("ontology_lookup", "ok"),
        ("sql", "ok"),
        ("check", "ok"),
        ("evaluate", "failed"),
        ("self_correct", "ok"),
        ("sql", "ok"),
        ("check", "ok"),
        ("evaluate", "ok"),
        ("memory_write", "ok"),
        ("answer", "ok"),
    ]
    assert result.steps[6].served_by.startswith("self_correct:")
    assert result.steps[7].served_by == "stub-correct" and result.steps[7].served_sql == [RIGHT_SQL]
    assert result.steps[9].served_memory_ids == [formula_id]
    assert extract.calls.count(RIGHT_SQL) == 3  # formula, corrected SQL, formula again
    assert result.checked is not None and result.checked.rows == [{"revenue_myr": 65.0}]


def test_self_correct_named_abstain_is_carried(extract: Extract, mem: SpaceMemory) -> None:
    _revenue_formula(mem)

    def give_up(context: PlanContext, candidate: Candidate, failure: Failure) -> Abstained:
        return Abstained("retries_exhausted", "two corrections still disagree")

    packager = Packager()
    result = _run(_ports(extract, Generator(WRONG_SQL), memory=mem, self_correct=give_up, package=packager))
    assert result.outcome == "abstain" and result.reason == "retries_exhausted"
    assert _by(result)["self_correct"].served_status == "refused"
    assert result.steps[-1].served_reason == "retries_exhausted: two corrections still disagree"
    assert packager.calls == []


def test_default_self_correct_abstains_on_the_failure(extract: Extract, mem: SpaceMemory) -> None:
    _revenue_formula(mem)
    gen = Generator(WRONG_SQL)
    result = _run(_ports(extract, gen, memory=mem))
    assert result.reason == "formula_mismatch"
    step = _by(result)["self_correct"]
    assert step.served_status == "skipped" and step.served_by == "self_correct:no_self_correct"
    assert len(gen.contexts) == 1, "the default never asks the generator again"


def test_packager_failure_is_a_named_abstain(extract: Extract) -> None:
    def broken(context: PlanContext, checked: Checked) -> dict[str, Any]:
        raise ValueError("no envelope")

    result = _run(_ports(extract, package=broken))
    assert result.outcome == "abstain" and result.reason == LoopReason.PACKAGE_FAILED.value
    assert result.envelope is None and result.checked is None
    assert "answer" not in _by(result)


# -- must-fail: wrong answers are caught -------------------------------------
def test_must_fail_wrong_answer_caught_by_evaluate(
    extract: Extract, mem: SpaceMemory, packager: Packager
) -> None:
    formula_id = _revenue_formula(mem)
    assert extract(WRONG_SQL) != extract(RIGHT_SQL)  # the stub really is wrong
    result = _run(_ports(extract, Generator(WRONG_SQL), memory=mem, package=packager))
    assert result.outcome == "abstain", "a wrong total was served"
    assert result.reason == LoopReason.FORMULA_MISMATCH.value
    by = _by(result)
    assert by["evaluate"].served_status == "failed"
    assert by["evaluate"].served_memory_ids == [formula_id]
    assert by["sql"].served_sql == [WRONG_SQL]
    assert result.steps[-1].served_step == "abstain"
    assert result.steps[-1].served_reason.startswith("formula_mismatch: ")
    assert "answer" not in by and packager.calls == [] and result.envelope is None


@pytest.mark.parametrize(
    ("sql", "rows", "reason"),
    [
        ("SELECT supplier_name FROM suppliers", [{"supplier_name": "x"}], LoopReason.UNGRANTED_TABLE),
        ("SELECT sku FROM inventory WHERE qty > 100", None, LoopReason.EMPTY_RESULT),
        ("SELECT SUM(qty) AS total FROM inventory WHERE qty > 100", None, LoopReason.NULL_RESULT),
        ("SELECT qty FROM inventory", [{"qty": math.inf}], LoopReason.NON_FINITE_VALUE),
        ("SELEC qty FRM", [{"qty": 1}], LoopReason.SQL_NOT_ANALYSABLE),
    ],
    ids=["ungranted_table", "empty_result", "null_result", "non_finite", "unanalysable"],
)
def test_must_fail_wrong_answer_caught_by_check(
    extract: Extract, packager: Packager, sql: str, rows: list[dict[str, Any]] | None, reason: LoopReason
) -> None:
    if rows is not None:
        extract.overrides[sql] = rows
    result = _run(_ports(extract, Generator(sql), package=packager))
    assert result.outcome == "abstain", f"{reason.value} was served as an answer"
    assert result.reason == reason.value
    by = _by(result)
    assert by["check"].served_status == "failed"
    assert "evaluate" not in by and "answer" not in by and packager.calls == []


# -- must-fail: an abstain always names its reason ----------------------------
def _abstain_step(reason: str) -> LoopStep:
    return LoopStep(seq=2, served_step="abstain", served_status="abstain", served_by="x", served_at="t", served_reason=reason)


def _plan_step() -> LoopStep:
    return LoopStep(seq=1, served_step="plan", served_status="ok", served_by="x", served_at="t")


def test_must_fail_abstain_without_named_reason(extract: Extract) -> None:
    with pytest.raises(ValidationError, match="abstain step"):
        AnalysisLoop(outcome="abstain", steps=[_plan_step()])
    for unnamed in ("", "something went wrong", "Bad Thing: x", "empty_result:", "empty_result: "):
        with pytest.raises(ValidationError, match="name its reason"):
            AnalysisLoop(outcome="abstain", steps=[_plan_step(), _abstain_step(unnamed)])
    with pytest.raises(ValidationError, match="answer step"):
        AnalysisLoop(outcome="answer", steps=[_plan_step(), _abstain_step("empty_result: no rows")])
    with pytest.raises(TypeError, match="snake_case reason"):
        loop_orchestrator._Abstain("Something Went Wrong", "a sentence is not a named reason")

    def unnamed_fix(context: PlanContext, candidate: Candidate, failure: Failure) -> Abstained:
        return Abstained("", "gave up")

    with pytest.raises(TypeError, match="snake_case reason"):
        _run(_ports(extract, Generator("SELECT sku FROM inventory WHERE qty > 100"), self_correct=unnamed_fix))
    with pytest.raises(TypeError, match="snake_case reason"):
        _run(_ports(extract, Generator(abstain="the engine said no", reason="no")))


@pytest.mark.parametrize(
    ("ports_kw", "gen_kw", "reason"),
    [
        ({"grant_error": "no session grant is bound"}, {}, LoopReason.UNGROUNDED_SESSION),
        ({"blocked": True}, {}, LoopReason.POLICY_BLOCKED),
        ({}, {"abstain": "no_trustworthy_path", "reason": "no metric"}, LoopReason.NO_TRUSTWORTHY_PATH),
        ({}, {"abstain": "engine_refused", "reason": "manifest"}, LoopReason.ENGINE_REFUSED),
        ({}, {"sql": None, "reason": "catalog"}, LoopReason.NOT_A_SQL_ANSWER),
    ],
    ids=["ungrounded", "blocked", "no_path", "engine_refused", "not_sql"],
)
def test_every_loop_abstain_names_its_reason(
    extract: Extract, ports_kw: dict[str, Any], gen_kw: dict[str, Any], reason: LoopReason
) -> None:
    sql = gen_kw.pop("sql", RIGHT_SQL)
    result = _run(_ports(extract, Generator(sql, **gen_kw), **ports_kw))
    assert result.outcome == "abstain"
    assert result.reason == reason.value
    last = result.steps[-1]
    assert (last.served_step, last.served_status) == ("abstain", "abstain")
    assert last.served_reason.startswith(f"{reason.value}: ") and len(last.served_reason) > len(reason.value) + 2
    assert all(s.served_status != "abstain" for s in result.steps[:-1])
    assert result.record().outcome == "abstain"


# -- memory: four kinds, Space isolation, no scored writes ---------------------
def test_must_fail_scored_round_loop_writes_no_memory(extract: Extract, mem: SpaceMemory) -> None:
    mem.confirm_solution(
        space_id="alpha", question=REVENUE_Q, sql=RIGHT_SQL, steward=STEWARD, source="steward:alice"
    )
    result = _run(_ports(extract, memory=mem), scored_pack_id="curated_ceo")
    by = _by(result)
    assert by["memory_write"].served_status == "refused", "the loop wrote memory on a scored pack"
    assert by["memory_write"].served_reason == sm.SCORED_PACK_WRITE
    assert result.reused is False and by["sql"].served_by == "engine:governed_metric"
    assert mem.read(space_id="alpha", kind="tool", actor="t")[0] == []
    assert "scored_round=True" in by["plan"].served_reason


def test_solution_reuse_reruns_sql_on_current_data(extract: Extract, mem: SpaceMemory) -> None:
    entry, _ = mem.confirm_solution(
        space_id="alpha", question=REVENUE_Q, sql=RIGHT_SQL, steward=STEWARD, source="steward:alice"
    )
    gen = Generator()
    first = _run(_ports(extract, gen, memory=mem))
    assert first.reused and first.checked is not None
    assert first.checked.rows == [{"revenue_myr": 65.0}] and first.checked.reused
    extract.con.execute("INSERT INTO transactions VALUES ('OUT','C',1,5)")
    before = len(extract.calls)
    second = _run(_ports(extract, gen, memory=mem))
    assert extract.calls[before] == RIGHT_SQL, "reuse must re-run the SQL"
    assert second.checked is not None and second.checked.rows == [{"revenue_myr": 70.0}]
    assert gen.contexts == []
    sql_step = _by(second)["sql"]
    assert sql_step.served_by == f"memory:{entry.id}@v1"
    assert sql_step.served_memory_ids == [entry.id]
    assert "not a validation" in sql_step.served_reason
    assert entry.id in second.memory_ids_read


def test_reused_solution_is_still_checked_and_evaluated(extract: Extract, mem: SpaceMemory) -> None:
    _revenue_formula(mem)
    mem.confirm_solution(
        space_id="alpha", question=REVENUE_Q, sql=WRONG_SQL, steward=STEWARD, source="steward:alice"
    )
    result = _run(_ports(extract, memory=mem))
    assert result.reused is True
    assert result.outcome == "abstain" and result.reason == LoopReason.FORMULA_MISMATCH.value


def test_table_formula_and_tool_memory_are_consumed(
    extract: Extract, mem: SpaceMemory, packager: Packager
) -> None:
    table, _ = mem.write(
        space_id="alpha",
        kind="table",
        key="inventory",
        body={"table": "inventory", "keys": ["sku"], "row_count": 6, "columns": ["sku", "qty", "bin"]},
        source="profile:alpha",
        actor="cortex:profiler",
    )
    formula_id = _revenue_formula(mem)
    first = _run(_ports(extract, memory=mem, package=packager), question="chart total revenue")
    (checked,) = packager.calls
    data_map = checked.tool_outputs["data_map"]
    node = next(n for n in data_map["nodes"] if n["table"] == "inventory")
    assert node["row_count"] == 6 and node["keys"] == ["sku"] and "bin" in node["columns"]
    assert node["memory_ids"] == [table.id]
    assert {e["from"] for e in data_map["edges"]} == {"transactions"}  # suppliers not granted
    assert {table.id, formula_id} <= set(first.memory_ids_read)
    assert checked.tool_outputs["chart_spec"]["type"] == "bignum"
    assert _by(first)["chart_spec"].served_tool == "chart_spec"

    written = _by(first)["memory_write"].served_memory_ids
    second = _run(_ports(extract, memory=mem), question="chart total revenue")
    assert written[0] in _by(second)["memory_lookup"].served_memory_ids
    tool_entry = mem.get(space_id="alpha", entry_id=written[0], actor="t")[0]
    assert tool_entry.body["tools"] == {"data_map": "ok", "chart_spec": "ok"}
    assert tool_entry.body["outcome"] == "answer" and tool_entry.version == 2


def test_tool_memory_plans_the_chart_for_a_question_it_solved(
    extract: Extract, mem: SpaceMemory, packager: Packager
) -> None:
    mem.write(
        space_id="alpha",
        kind="tool",
        key=f"loop:{sm.question_key(REVENUE_Q)}",
        body={"tools": {"chart_spec": "ok"}},
        source="loop:earlier",
        actor=loop_orchestrator.LOOP_ACTOR,
    )
    result = _run(_ports(extract, memory=mem, package=packager))
    assert "chart_spec planned from tool memory" in _by(result)["memory_lookup"].served_reason
    assert packager.calls[0].tool_outputs["chart_spec"] == {
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
    gen = Generator()
    result = _run(_ports(extract, gen, memory=mem, space_id="beta"))
    assert entry.id not in result.memory_ids_read
    assert all(entry.id not in s.served_memory_ids for s in result.steps)
    assert all(entry.id not in str(c) for c in gen.contexts)
    assert result.outcome == "answer" and result.reused is False


# -- tools via the recorded transcript ----------------------------------------
def test_chart_and_data_map_on_recorded_c_stamp_transcript(extract: Extract, packager: Packager) -> None:
    body = json.loads((C_STAMP / "resp_body.json").read_text(encoding="utf-8"))
    recorded_sql = body["choices"][0]["message"]["content"].strip().rstrip(";")
    served = f"engine:generated ({body['served_provider']}/{body['served_model']}, recorded)"

    def replay(context: PlanContext) -> Candidate:
        return Candidate(sql=recorded_sql, served_by=served)

    result = _run(_ports(extract, replay, package=packager), question="plot some skus from inventory")
    assert result.outcome == "answer"
    sql_step = _by(result)["sql"]
    assert sql_step.served_by == served and sql_step.served_sql == [recorded_sql]
    assert extract.calls == [recorded_sql]
    tools = packager.calls[0].tool_outputs
    assert set(tools) == {"data_map", "chart_spec"}
    assert {n["table"] for n in tools["data_map"]["nodes"]} == set(GRANTED)
    assert tools["chart_spec"]["type"] == "none"
    assert tools["chart_spec"]["reason"] == "no numeric column"
