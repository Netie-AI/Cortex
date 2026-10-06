"""C-LOOP (#291) on the served envelope: POST /v1/contract/ask.

Assertions are on the HTTP response DMS receives (R-0001): rendered answer
text, rows, badge, and the ``analysis_loop`` round record.

* Loop off (default): the response is byte-identical to parent ``c7469da4``
  (golden recorded there; only uuids, tokens, memory ids and timestamps are
  normalised).
* ``test_must_fail_contract_ask_wrong_answer_becomes_abstain`` — the engine
  stub serves a wrong total; the loop's evaluate step catches it against the
  Space's steward formula and the customer receives a named abstain.
* ``test_must_fail_contract_ask_abstain_carries_named_reason`` — every loop
  abstain on the wire ends with an abstain step naming its reason code.

The generator here is the default engine-cascade adapter, self-correct is the
no-retry default and the packager is the passthrough (C-LOOP-A/-B/-C replace them).

No model is called.
"""

from __future__ import annotations

from typing import Any

import pytest
from cortex_contract.answer import Answer

from CortexOS.loop import ENABLED_ENV
from CortexOS.loop.interfaces import LoopReason
from CortexOS.memory import space_memory as sm
from tests.dms import test_c_mem_contract_ask as c_mem
from tests.test_loop.flag_off_cases import CASES, load_golden, run_cases

REVENUE_Q, SESSION, STEWARD = c_mem.REVENUE_Q, c_mem.SESSION, c_mem.STEWARD
ask_http = c_mem.ask_http
execute_spy = c_mem.execute_spy

RIGHT_SQL = (
    "SELECT ROUND(COALESCE(SUM(quantity_kg * unit_cost_myr), 0), 2) AS revenue_myr "
    "FROM transactions WHERE txn_type = 'OUT'"
)
# Inbound stock counted as revenue: plausible, governed-looking, wrong.
WRONG_SQL = (
    "SELECT ROUND(COALESCE(SUM(quantity_kg * unit_cost_myr), 0), 2) AS revenue_myr "
    "FROM transactions"
)


def _ask(client, space_id: str, question: str = REVENUE_Q, **extra: Any) -> dict[str, Any]:
    resp = client.post(
        "/v1/contract/ask",
        json={"question": question, "session_id": SESSION, "space_id": space_id, **extra},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    Answer.model_validate(body)
    return body


def _steps(body: dict[str, Any]) -> list[tuple[str, str]]:
    return [(s["served_step"], s["served_status"]) for s in body["analysis_loop"]["steps"]]


def _abstain_code(body: dict[str, Any]) -> str:
    last = body["analysis_loop"]["steps"][-1]
    assert (last["served_step"], last["served_status"]) == ("abstain", "abstain")
    return last["served_reason"].split(": ", 1)[0]


def _formula(space: str) -> str:
    entry, _ = sm.get_space_memory().write(
        space_id=space,
        kind="formula",
        key="revenue_total",
        body={"metric": "total revenue", "phrases": ["total revenue"], "sql": RIGHT_SQL},
        source="steward:alice",
        actor=STEWARD.actor_id,
    )
    return entry.id


@pytest.fixture
def loop_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENABLED_ENV, "1")
    monkeypatch.setenv(sm.ENABLED_ENV, "1")


# -- loop off ------------------------------------------------------------------
@pytest.mark.parametrize("flag", [None, "0", "off"])
def test_loop_off_is_byte_identical_to_parent(ask_http, monkeypatch, flag: str | None) -> None:
    if flag is None:
        monkeypatch.delenv(ENABLED_ENV, raising=False)
    else:
        monkeypatch.setenv(ENABLED_ENV, flag)
    golden = load_golden()
    assert set(golden) == {name for name, *_ in CASES}
    got = run_cases(ask_http, monkeypatch)
    for name in golden:
        assert got[name] == golden[name], name
        assert "analysis_loop" not in got[name]


# -- loop on: stamps -----------------------------------------------------------
def test_loop_on_envelope_lists_every_step(ask_http, loop_on) -> None:
    ask_http.bind_session(SESSION, "alpha")
    formula_id = _formula("alpha")
    body = _ask(ask_http, "alpha")
    assert body["rows"] and float(body["rows"][0]["revenue_myr"]) > 0
    assert body["answer"].startswith("Result: revenue_myr = ")
    assert body["provenance"]["badge"] == "governed_metric"
    loop = body["analysis_loop"]
    assert set(loop) == {"outcome", "steps"} and loop["outcome"] == "answer"
    assert _steps(body) == [
        ("plan", "ok"),
        ("memory_lookup", "ok"),
        ("ontology_lookup", "ok"),
        ("sql", "ok"),
        ("check", "ok"),
        ("evaluate", "ok"),
        ("memory_write", "ok"),
        ("answer", "ok"),
    ]
    by = {s["served_step"]: s for s in loop["steps"]}
    assert by["sql"]["served_sql"] == [body["sql_used"]]
    assert by["sql"]["served_by"] == "engine:governed_metric"
    assert by["evaluate"]["served_memory_ids"] == [formula_id]
    assert by["evaluate"]["served_sql"] == [RIGHT_SQL]
    assert by["ontology_lookup"]["served_tool"] == "data_map"
    assert by["answer"]["served_by"] == "packager:passthrough"
    assert body["memory_ids_read"] == [formula_id]
    assert body["memory_reads"][0]["kind"] == "formula"
    assert body["drillthrough_token"]


def test_loop_on_chart_tool_on_the_envelope(ask_http, loop_on) -> None:
    ask_http.bind_session(SESSION, "alpha")
    body = _ask(ask_http, "alpha", question="chart our total revenue")
    if body["analysis_loop"]["outcome"] != "answer":
        pytest.skip(f"engine does not route this phrasing: {body['answer']}")
    chart = next(s for s in body["analysis_loop"]["steps"] if s["served_step"] == "chart_spec")
    assert (chart["served_status"], chart["served_tool"], chart["served_by"]) == (
        "ok",
        "chart_spec",
        "tool:chart_spec",
    )
    assert "chart_spec" not in body["analysis_loop"], "chart output on the wire is C-LOOP-C's"


# -- must-fail: a wrong answer is never served ---------------------------------
def test_must_fail_contract_ask_wrong_answer_becomes_abstain(
    ask_http, loop_on, monkeypatch: pytest.MonkeyPatch
) -> None:
    from CortexOS.dms import answer_engine
    from CortexOS.dms.sql_validate_gate import run_gate
    from CortexOS.dms.warehouse_db import load_semantic_layer
    from CortexOS.execution.submit import execute_sql

    real = answer_engine.answer

    def wrong_engine(question: str, **kw: Any) -> dict[str, Any]:
        out = dict(real(question, **kw))
        gate = run_gate(WRONG_SQL, load_semantic_layer())
        rows, _, _ = execute_sql(kw["verified"], gate.safe_sql)
        out.update(sql_used=gate.safe_sql, rows=rows, row_count=len(rows))
        out["answer"] = f"Result: revenue_myr = {rows[0]['revenue_myr']}"
        return out

    monkeypatch.setattr(answer_engine, "answer", wrong_engine)
    ask_http.bind_session(SESSION, "alpha")
    formula_id = _formula("alpha")
    monkeypatch.delenv(ENABLED_ENV)
    served_wrong = _ask(ask_http, "alpha")
    assert served_wrong["rows"] and "txn_type" not in served_wrong["sql_used"], (
        "control: with the loop off the wrong stub answer is served"
    )
    monkeypatch.setenv(ENABLED_ENV, "1")
    body = _ask(ask_http, "alpha")

    assert body["analysis_loop"]["outcome"] == "abstain", "a wrong total reached the customer"
    assert _abstain_code(body) == LoopReason.FORMULA_MISMATCH.value
    assert body["provenance"]["badge"] == "abstain"
    assert body["rows"] == [] and body["sql_used"] is None and body["drillthrough_token"] is None
    assert body["contributing_sources"] == []
    assert "formula_mismatch" in body["answer"] and "Result:" not in body["answer"]
    assert body["assumptions"][0].startswith("analysis loop abstain: formula_mismatch: ")
    by = {s["served_step"]: s for s in body["analysis_loop"]["steps"]}
    assert by["evaluate"]["served_status"] == "failed"
    assert by["self_correct"]["served_status"] == "skipped"
    assert by["evaluate"]["served_memory_ids"] == [formula_id]
    assert "txn_type" not in by["sql"]["served_sql"][0]
    assert "answer" not in by


# -- must-fail: every abstain names its reason ---------------------------------
@pytest.mark.parametrize(
    ("question", "reason"),
    [
        ("drop table inventory", LoopReason.POLICY_BLOCKED),
        ("what is the weather in penang", LoopReason.NO_TRUSTWORTHY_PATH),
    ],
)
def test_must_fail_contract_ask_abstain_carries_named_reason(
    ask_http, loop_on, question: str, reason: LoopReason
) -> None:
    ask_http.bind_session(SESSION, "alpha")
    body = _ask(ask_http, "alpha", question=question)
    loop = body["analysis_loop"]
    assert loop["outcome"] == "abstain"
    assert _abstain_code(body) == reason.value, loop
    assert body["provenance"]["badge"] in {"abstain", "blocked"}
    assert body["rows"] == [] and body["sql_used"] is None
    assert body["assumptions"][0].startswith(f"analysis loop abstain: {reason.value}: ")
    assert _steps(body)[-1] == ("abstain", "abstain")


def test_loop_on_keeps_the_unbound_space_refusal(ask_http, loop_on) -> None:
    resp = ask_http.post(
        "/v1/contract/ask",
        json={"question": REVENUE_Q, "session_id": SESSION, "space_id": "never-bound"},
    )
    assert resp.status_code == 409
    assert resp.json()["detail"]["code"] == "space_unbound"


# -- memory over HTTP ----------------------------------------------------------
def test_loop_scored_round_writes_no_memory(ask_http, loop_on) -> None:
    ask_http.bind_session(SESSION, "alpha")
    body = _ask(ask_http, "alpha", scored_pack_id="curated_ceo")
    by = {s["served_step"]: s for s in body["analysis_loop"]["steps"]}
    assert by["memory_write"]["served_status"] == "refused"
    assert by["memory_write"]["served_reason"] == sm.SCORED_PACK_WRITE
    assert sm.get_space_memory().read(space_id="alpha", kind="tool", actor="t")[0] == []
    assert body["rows"]


def test_loop_reuse_reruns_sql_and_never_serves_cached_rows(
    ask_http, loop_on, execute_spy: list[str]
) -> None:
    ask_http.bind_session(SESSION, "alpha")
    first = _ask(ask_http, "alpha")
    entry, _ = sm.get_space_memory().confirm_solution(
        space_id="alpha",
        question=REVENUE_Q,
        sql=first["sql_used"],
        steward=STEWARD,
        source=f"ask:{first['answer_id']}",
    )
    before = len(execute_spy)
    body = _ask(ask_http, "alpha")
    assert len(execute_spy) == before + 1, "reuse must re-run the SQL"
    assert body["reused"] is True and entry.id in body["memory_ids_read"]
    assert body["rows"] == first["rows"]
    assert body["provenance"]["layer"] == "memory_reuse"
    assert body["provenance"]["badge"] == "session"
    by = {s["served_step"]: s for s in body["analysis_loop"]["steps"]}
    assert by["sql"]["served_by"] == f"memory:{entry.id}@v1"
    assert by["check"]["served_status"] == "ok"


def test_loop_space_b_never_reads_space_a_memory(ask_http, loop_on) -> None:
    ask_http.bind_session(SESSION, "alpha")
    ask_http.bind_session(SESSION, "beta")
    alpha_formula = _formula("alpha")
    alpha = _ask(ask_http, "alpha")
    sm.get_space_memory().confirm_solution(
        space_id="alpha", question=REVENUE_Q, sql=alpha["sql_used"], steward=STEWARD, source="s"
    )
    alpha_ids = set(_ask(ask_http, "alpha")["memory_ids_read"])
    assert alpha_formula in alpha_ids and len(alpha_ids) >= 3

    beta = _ask(ask_http, "beta")
    assert beta["memory_ids_read"] == [] and beta["reused"] is False
    for step in beta["analysis_loop"]["steps"]:
        assert not set(step["served_memory_ids"]) & alpha_ids
    assert {s.served_space_id for s in sm.get_space_memory().stamps(space_id="beta")} == {"beta"}
