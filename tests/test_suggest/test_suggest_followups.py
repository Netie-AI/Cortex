"""SUGGEST (#308) core: templated follow-ups and the grounding refusal.

Runs on a hand-built schema so the assertions do not depend on pack data. The
``test_must_fail_*`` cases are rerun with the refusal switched off by
tests/test_suggest/test_suggest_guard_mutations.py and must fail there.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from CortexOS.suggest import followups as fu
from CortexOS.suggest.followups import Candidate, Join, Rephrased, Result, Schema, Table

NOW = "2026-10-06T00:00:00+00:00"

SCHEMA = Schema(
    tables={
        "orders": Table(
            "orders",
            ("order_id", "customer_id", "region", "status", "amount", "placed_at"),
            {
                "order_id": "string",
                "customer_id": "string",
                "region": "string",
                "status": "string",
                "amount": "number",
                "placed_at": "date",
            },
            primary_key="order_id",
        ),
        "customers": Table(
            "customers",
            ("customer_id", "segment"),
            {"customer_id": "string", "segment": "string"},
            primary_key="customer_id",
        ),
        "payroll": Table("payroll", ("employee", "salary"), {"employee": "string", "salary": "number"}),
    },
    joins=(Join("orders", "customer_id", "customers", "customer_id"),),
    hidden=frozenset({"email"}),
)

ROWS = (
    {"region": "North", "revenue": 120.5},
    {"region": "South", "revenue": 80.0},
    {"region": "East", "revenue": 10.0},
)


def _result(granted: set[str] | None = None, rows=ROWS, tables=("orders",)) -> Result:
    return Result(
        question="revenue by region",
        rows=rows,
        tables=tables,
        granted=frozenset(granted if granted is not None else {"orders", "customers"}),
    )


def _names(served: list[dict]) -> set[str]:
    out: set[str] = set()
    for f in served:
        out.update(f["tables"])
        out.update(c.rsplit(".", 1)[-1] for c in f["columns"])
    return out


def test_templates_cover_drill_compare_trend_and_join_neighbour() -> None:
    outcome = fu.suggest(_result(), SCHEMA, now=NOW)
    assert outcome.reason is None
    assert fu.MIN_FOLLOWUPS <= len(outcome.followups) <= fu.MAX_FOLLOWUPS
    by_kind = {f["kind"]: f for f in outcome.followups}
    assert set(by_kind) == {"drill_down", "compare", "trend", "join_neighbour"}
    assert outcome.followups[0]["question"] == "Show the orders rows where region is 'North'."
    assert by_kind["compare"]["values"] == ["North", "South"]
    assert by_kind["trend"]["columns"] == ["revenue", "orders.placed_at"]
    assert by_kind["join_neighbour"]["tables"] == ["orders", "customers"]
    for f in outcome.followups:
        assert f["served_by"] == fu.SERVED_BY
        assert f["served_at"] == NOW
        assert f["served_reason"].startswith("template:")
        assert f["served_provider"] is None and f["served_model"] is None
        assert f["served_local"] is False


def test_followups_are_deterministic() -> None:
    assert fu.suggest(_result(), SCHEMA, now=NOW) == fu.suggest(_result(), SCHEMA, now=NOW)


def test_every_served_followup_names_only_granted_schema_and_result() -> None:
    result = _result()
    outcome = fu.suggest(result, SCHEMA, now=NOW)
    values = result.values()
    for f in outcome.followups:
        assert set(f["tables"]) <= {"orders", "customers"}
        for col in f["columns"]:
            if "." in col:
                table, name = col.split(".")
                assert table in f["tables"] and name in SCHEMA.tables[table].columns
            else:
                assert col in result.columns
        assert set(f["values"]) <= values


def test_join_neighbour_outside_the_grant_is_never_proposed() -> None:
    outcome = fu.suggest(_result(granted={"orders"}), SCHEMA, now=NOW)
    assert outcome.followups
    assert "customers" not in _names(outcome.followups)
    assert all("customers" not in f["question"] for f in outcome.followups)


def test_no_followups_when_fewer_than_two_are_grounded() -> None:
    one_measure_no_schema = Schema(tables={"orders": Table("orders", ("amount",), {"amount": "number"})})
    outcome = fu.suggest(_result(granted={"orders"}), one_measure_no_schema, now=NOW)
    assert outcome.followups == []
    assert outcome.reason == "fewer than 2 grounded follow-ups"


@pytest.mark.parametrize(
    "result",
    [
        _result(rows=()),
        _result(tables=("payroll",)),
        _result(tables=()),
        _result(granted=set()),
    ],
    ids=["no_rows", "read_ungranted_table", "no_table", "no_grant"],
)
def test_nothing_is_proposed_without_rows_or_a_granted_read(result: Result) -> None:
    assert fu.propose(result, SCHEMA) == []
    assert fu.suggest(result, SCHEMA, now=NOW).followups == []


def test_time_valued_result_gets_no_trend() -> None:
    rows = ({"placed_at": datetime(2026, 1, 1), "revenue": 1.0}, {"placed_at": datetime(2026, 2, 1), "revenue": 2.0})
    kinds = {c.kind for c in fu.propose(_result(rows=rows), SCHEMA)}
    assert "trend" not in kinds


GOOD = Candidate(
    "Show the orders rows where region is 'North'.",
    "drill_down", ("orders",), ("orders.region",), ("North",), "drill_down:returned_dimension",
)

BAD = {
    "ungranted_table": Candidate(
        "Break down revenue by salary in payroll.",
        "drill_down", ("payroll",), ("revenue", "payroll.salary"), (), "t",
    ),
    "table_not_in_schema": Candidate(
        "Break down revenue by refund in refunds.",
        "drill_down", ("refunds",), ("revenue",), (), "t",
    ),
    "column_not_in_schema": Candidate(
        "Break down revenue by profit_margin in orders.",
        "drill_down", ("orders",), ("revenue", "orders.profit_margin"), (), "t",
    ),
    "hidden_column": Candidate(
        "Break down revenue by email in customers.",
        "drill_down", ("customers",), ("revenue", "customers.email"), (), "t",
    ),
    "column_of_ungranted_table": Candidate(
        "Break down revenue by salary in orders.",
        "drill_down", ("orders",), ("revenue", "payroll.salary"), (), "t",
    ),
    "bare_column_not_in_result": Candidate(
        "Break down margin by region in orders.",
        "drill_down", ("orders",), ("margin", "orders.region"), (), "t",
    ),
    "value_not_in_result": Candidate(
        "Show the orders rows where region is 'West'.",
        "drill_down", ("orders",), ("orders.region",), ("West",), "t",
    ),
    "text_names_ungrounded_identifier": Candidate(
        "Show the orders rows where region is 'North' by profit_margin.",
        "drill_down", ("orders",), ("orders.region",), ("North",), "t",
    ),
    "text_names_ungranted_table": Candidate(
        "Show the orders rows where region is 'North' joined to payroll.",
        "drill_down", ("orders",), ("orders.region",), ("North",), "t",
    ),
    "text_names_hidden_column": Candidate(
        "Show the orders rows where region is 'North' with email.",
        "drill_down", ("orders",), ("orders.region",), ("North",), "t",
    ),
    "text_quotes_unreferenced_value": Candidate(
        "Show the orders rows where region is 'North' or 'West'.",
        "drill_down", ("orders",), ("orders.region",), ("North",), "t",
    ),
}


def test_a_grounded_candidate_is_not_refused() -> None:
    assert fu.refuse(GOOD, _result(), SCHEMA) is None


@pytest.mark.parametrize("case", sorted(BAD))
def test_must_fail_suggestion_outside_grant_or_schema_is_rejected(case: str) -> None:
    why = fu.refuse(BAD[case], _result(), SCHEMA)
    assert why is not None, f"{case} was not refused"


class _Widen:
    """A model that rewords every follow-up to name something it may not."""

    def __init__(self, text: str) -> None:
        self.text = text
        self.calls: list[tuple[str, list[str]]] = []

    def __call__(self, question: str, templates) -> Rephrased:
        self.calls.append((question, list(templates)))
        return Rephrased(
            texts=tuple(self.text for _ in templates),
            served_by="openvault-freeroute",
            served_reason="FreeRoute call test",
            served_provider="groq",
            served_model="openai/gpt-oss-120b",
        )


@pytest.mark.parametrize(
    "text",
    [
        "What is the salary in payroll?",
        "Break down revenue by profit_margin in orders.",
        "Which customers have an email on file?",
        "Show the orders rows where region is 'West'.",
    ],
    ids=["ungranted_table", "invented_column", "hidden_column", "invented_value"],
)
def test_must_fail_model_rewording_that_widens_is_not_served(text: str) -> None:
    model = _Widen(text)
    outcome = fu.suggest(_result(granted={"orders"}), SCHEMA, rephrase=model, now=NOW)
    assert model.calls, "the model was not asked"
    assert outcome.followups
    for f in outcome.followups:
        assert f["question"] != text
        assert f["served_by"] == fu.SERVED_BY
    assert len(outcome.refused) >= len(outcome.followups)


def test_model_rewording_that_stays_grounded_is_served_and_stamped() -> None:
    def reword(question: str, templates) -> Rephrased:
        return Rephrased(
            texts=tuple(t.replace("Show the", "List the") for t in templates),
            served_by="openvault-freeroute",
            served_reason="FreeRoute call abc",
            served_provider="groq",
            served_model="openai/gpt-oss-120b",
        )

    outcome = fu.suggest(_result(), SCHEMA, rephrase=reword, now=NOW)
    first = outcome.followups[0]
    assert first["question"] == "List the orders rows where region is 'North'."
    assert first["served_by"] == "openvault-freeroute"
    assert first["served_provider"] == "groq" and first["served_model"] == "openai/gpt-oss-120b"
    assert "rephrased (FreeRoute call abc)" in first["served_reason"]


def test_model_only_sees_masked_templates() -> None:
    model = _Widen("x")
    fu.suggest(_result(), SCHEMA, rephrase=model, now=NOW)
    (_, templates), = model.calls
    sent = " ".join(templates)
    assert "{v1}" in sent
    for value in ("North", "South", "East", "120.5", "80.0"):
        assert value not in sent


def test_a_raising_model_falls_back_to_templates() -> None:
    def boom(question: str, templates) -> Rephrased:
        raise RuntimeError("provider down")

    outcome = fu.suggest(_result(), SCHEMA, rephrase=boom, now=NOW)
    assert outcome.followups == fu.suggest(_result(), SCHEMA, now=NOW).followups
