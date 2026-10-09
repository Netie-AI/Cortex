"""SUGGEST (#308) on the served envelope: POST /v1/contract/ask.

Assertions are on the HTTP response DMS receives (R-0001): the rendered answer
and rows, plus ``followups`` / ``followups_reason``. Off, the envelope is
byte-identical to contract 1.4.0. On, follow-ups name only granted tables,
visible ontology columns and returned values, and never appear on an abstain
or a clarify. No model is called here.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

from CortexOS.suggest import ask as suggest_ask
from CortexOS.suggest import followups as fu

ROOT = Path(__file__).resolve().parents[2]
TOP_SKUS = "top 5 skus by revenue"
TOTAL = "what is our total revenue"
ONTOLOGY_TABLES = {"inventory", "suppliers", "locations", "shipments", "transactions", "alerts"}


def _frozen_answer_fields(version: str) -> set[str]:
    spec = json.loads((ROOT / "contract" / f"openapi-{version}.json").read_text(encoding="utf-8"))
    return set(spec["components"]["schemas"]["ContractAnswer"]["properties"])


def assert_grounded(body: dict[str, Any], grant: set[str]) -> None:
    schema = suggest_ask.load_schema()
    returned = {str(v) for row in body["rows"] for v in row.values() if v is not None}
    result_columns = {k for row in body["rows"] for k in row}
    for f in body["followups"]:
        assert f["tables"] and set(f["tables"]) <= grant, f
        for col in f["columns"]:
            if "." in col:
                table, name = col.split(".", 1)
                assert table in grant and name in schema.tables[table].columns, f
            else:
                assert col in result_columns, f
        assert set(f["values"]) <= returned, f
        for other in ONTOLOGY_TABLES - grant:
            assert other not in f["question"], f


def test_contract_ask_off_is_byte_identical_and_never_runs_the_seam(
    ask_http, monkeypatch: pytest.MonkeyPatch, deterministic
) -> None:
    def _never(*_a: Any, **_k: Any) -> Any:
        raise AssertionError("follow-ups ran with CORTEX_SUGGEST off")

    monkeypatch.setattr(fu, "suggest", _never)
    monkeypatch.setattr(suggest_ask, "load_schema", _never)
    ask_http.bind({"transactions": "TRUE"})
    deterministic()
    off = ask_http.ask(TOP_SKUS)

    monkeypatch.setattr(suggest_ask, "attach_followups", lambda data, **_k: data)
    ask_http.bind({"transactions": "TRUE"})
    deterministic()
    seam_removed = ask_http.ask(TOP_SKUS)

    assert off.content == seam_removed.content
    body = off.json()
    assert body["rows"] and body["answer"] and body["sql_used"]
    assert set(body) == _frozen_answer_fields("1.4.0")
    assert b"followups" not in off.content


def test_contract_ask_on_serves_grounded_followups_without_changing_the_answer(
    ask_http, monkeypatch: pytest.MonkeyPatch
) -> None:
    ask_http.bind({"transactions": "TRUE"})
    off = ask_http.ask(TOP_SKUS).json()
    monkeypatch.setenv(suggest_ask.ENABLED_ENV, "1")
    ask_http.bind({"transactions": "TRUE"})
    on = ask_http.ask(TOP_SKUS).json()

    assert on["answer"] == off["answer"] and on["rows"] == off["rows"]
    assert on["sql_used"] == off["sql_used"] and on["provenance"] == off["provenance"]
    assert set(on) == _frozen_answer_fields("1.5.0")
    assert on["followups_reason"] is None
    assert fu.MIN_FOLLOWUPS <= len(on["followups"]) <= fu.MAX_FOLLOWUPS
    top_sku = on["rows"][0]["sku"]
    assert on["followups"][0]["question"] == f"Show the transactions rows where sku is '{top_sku}'."
    assert {f["kind"] for f in on["followups"]} >= {"drill_down", "compare", "trend"}
    for f in on["followups"]:
        assert f["served_by"] == fu.SERVED_BY and f["served_reason"].startswith("template:")
        datetime.fromisoformat(f["served_at"])
    assert_grounded(on, {"transactions"})


def test_contract_ask_followups_stay_inside_the_grant(
    ask_http, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(suggest_ask.ENABLED_ENV, "1")
    ask_http.bind({"transactions": "TRUE"})
    narrow = ask_http.ask(TOTAL).json()
    assert float(narrow["rows"][0]["revenue_myr"]) > 0
    assert narrow["followups"]
    assert_grounded(narrow, {"transactions"})
    assert all(f["kind"] != "join_neighbour" for f in narrow["followups"])

    ask_http.bind({"transactions": "TRUE", "inventory": "TRUE"})
    wide = ask_http.ask(TOTAL).json()
    assert_grounded(wide, {"transactions", "inventory"})
    (neighbour,) = [f for f in wide["followups"] if f["kind"] == "join_neighbour"]
    assert neighbour["tables"] == ["transactions", "inventory"]
    assert neighbour["columns"] == ["transactions.sku", "inventory.sku"]


CLARIFY = "clarify: no follow-ups on a clarify"
ABSTAIN = "abstain: no follow-ups on an abstain"


@pytest.mark.parametrize(
    "question",
    ["revenue by location", "what is the weather", "drop table inventory", "stock by category"],
)
def test_must_fail_contract_ask_abstain_or_clarify_carries_no_followups(
    ask_http, monkeypatch: pytest.MonkeyPatch, question: str
) -> None:
    monkeypatch.setenv(suggest_ask.ENABLED_ENV, "1")
    ask_http.bind({"transactions": "TRUE"})
    body = ask_http.ask(question).json()
    assert body["provenance"]["badge"] in {"abstain", "blocked"}
    assert body["answer"]
    assert body["followups"] == []
    clarify = body["route"] == "needs_clarification"
    assert body["followups_reason"] == (CLARIFY if clarify else ABSTAIN)
    if question == "drop table inventory":
        assert body["route"] == "blocked" and body["followups_reason"] == ABSTAIN
