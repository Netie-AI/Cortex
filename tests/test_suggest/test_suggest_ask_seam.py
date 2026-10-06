"""SUGGEST (#308) ask seam: flag, abstain/clarify refusal, grant from the manifest.

The abstain and clarify shapes below still carry rows and SQL, so the only thing
standing between them and a follow-up is ``not_answered``. They are rerun with
that guard switched off by test_suggest_guard_mutations.py and must fail there.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from cortex_contract.answer import Answer, Badge, Provenance

from CortexOS.suggest import ask as seam
from CortexOS.suggest.followups import Join, Schema, Table

SCHEMA = Schema(
    tables={
        "orders": Table(
            "orders",
            ("order_id", "customer_id", "region", "amount", "placed_at"),
            {"order_id": "string", "customer_id": "string", "region": "string", "amount": "number", "placed_at": "date"},
            primary_key="order_id",
        ),
        "customers": Table("customers", ("customer_id", "segment"), {"customer_id": "string", "segment": "string"}),
    },
    joins=(Join("orders", "customer_id", "customers", "customer_id"),),
)
GRANT = SimpleNamespace(row_predicates={"orders": "TRUE", "customers": "TRUE"})
SQL = "SELECT region, SUM(amount) AS revenue FROM orders GROUP BY region"
ROWS = [{"region": "North", "revenue": 120.5}, {"region": "South", "revenue": 80.0}]


def _answer(badge: str = "session", **extra: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "answer": "North leads.",
        "sql_used": SQL,
        "audit_id": "a1",
        "route": "sql",
        "rows": [dict(r) for r in ROWS],
        "provenance": {"layer": "engine", "badge": badge},
    }
    data.update(extra)
    return data


def _attach(data: dict[str, Any]) -> dict[str, Any]:
    return seam.attach_followups(data, question="revenue by region", verified=GRANT, schema=SCHEMA)


@pytest.fixture
def on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(seam.ENABLED_ENV, "1")
    monkeypatch.delenv("CORTEX_SUGGEST_MODEL", raising=False)


def test_off_returns_the_same_dict_untouched(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(seam.ENABLED_ENV, raising=False)
    data = _answer()
    before = dict(data)
    assert _attach(data) is data
    assert data == before
    assert "followups" not in Answer.model_validate(data).model_dump_json()


def test_on_answered_result_gets_followups(on: None) -> None:
    out = _attach(_answer())
    assert out["followups_reason"] is None
    assert 2 <= len(out["followups"]) <= 5
    assert out["followups"][0]["question"] == "Show the orders rows where region is 'North'."
    wire = Answer.model_validate(out).model_dump()
    assert wire["followups"] and wire["followups_reason"] is None


def test_grant_comes_from_the_manifest_not_the_schema(on: None) -> None:
    narrow = SimpleNamespace(row_predicates={"orders": "TRUE"})
    out = seam.attach_followups(_answer(), question="q", verified=narrow, schema=SCHEMA)
    assert out["followups"]
    assert all("customers" not in f["tables"] for f in out["followups"])
    none = seam.attach_followups(
        _answer(), question="q", verified=SimpleNamespace(row_predicates={}), schema=SCHEMA
    )
    assert none["followups"] == [] and none["followups_reason"] == "no session grant"


ABSTAIN_SHAPES = {
    "badge_abstain": _answer(badge="abstain"),
    "badge_blocked": _answer(badge="blocked"),
    "route_refused": _answer(route="refused"),
    "layer_abstain": _answer(layer="abstain"),
    "unmapped_badge": _answer(badge="document"),
    "provenance_model_abstain": _answer(
        provenance=Provenance(layer="generated", badge=Badge.ABSTAIN)
    ),
}
CLARIFY_SHAPES = {
    "route_needs_clarification": _answer(route="needs_clarification"),
    "route_clarify": _answer(route="clarify"),
    "clarification_payload": _answer(clarification={"question": "Which region?"}),
    "clarify_flag": _answer(clarify=True),
}


@pytest.mark.parametrize("shape", sorted(ABSTAIN_SHAPES))
def test_must_fail_no_followups_on_an_abstain(on: None, shape: str) -> None:
    out = _attach(dict(ABSTAIN_SHAPES[shape]))
    assert out["followups"] == []
    assert out["followups_reason"] == "abstain: no follow-ups on an abstain"


@pytest.mark.parametrize("shape", sorted(CLARIFY_SHAPES))
def test_must_fail_no_followups_on_a_clarify(on: None, shape: str) -> None:
    out = _attach(dict(CLARIFY_SHAPES[shape]))
    assert out["followups"] == []
    assert out["followups_reason"] == "clarify: no follow-ups on a clarify"


@pytest.mark.parametrize(
    ("extra", "reason"),
    [({"sql_used": None}, "no served SQL"), ({"rows": []}, "no result rows")],
)
def test_nothing_served_gets_no_followups(on: None, extra: dict[str, Any], reason: str) -> None:
    out = _attach(_answer(**extra))
    assert out["followups"] == [] and out["followups_reason"] == reason


def test_missing_ontology_fails_closed(on: None, monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom() -> Schema:
        raise FileNotFoundError("no ontology")

    monkeypatch.setattr(seam, "load_schema", _boom)
    out = seam.attach_followups(_answer(), question="q", verified=GRANT)
    assert out["followups"] == []
    assert out["followups_reason"] == "schema unavailable: FileNotFoundError"


def test_sql_tables_reads_tables_not_ctes() -> None:
    sql = "WITH t AS (SELECT * FROM orders) SELECT * FROM t JOIN customers c ON TRUE"
    assert sorted(seam.sql_tables(sql)) == ["customers", "orders"]
    assert seam.sql_tables("not sql at all ((") == ()


def test_dms_ontology_schema_hides_non_visible_columns(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PACK", "dms")
    schema = seam.load_schema()
    assert "email" not in schema.tables["suppliers"].columns
    assert {"email", "phone", "contact_person"} <= schema.hidden
    assert any(j.from_table == "transactions" and j.to_table == "inventory" for j in schema.joins)
