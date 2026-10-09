"""C-LOOP-C (#305): result packaging, pure.

The insight may only state numbers read off the returned rows, the chart spec
may only name row columns, and an abstain is never packaged. No model is
called: the writer below is a stand-in that returns fixed text.
"""

from __future__ import annotations

import ast
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

import pytest
from cortex_contract.answer import Answer, Badge, InsightFact, Provenance

from CortexOS.dms import result_package as rp

ROOT = Path(__file__).resolve().parents[2]
SQL = "SELECT sku, sales_value_myr FROM transactions ORDER BY 2 DESC LIMIT 3"
ROWS: list[dict[str, Any]] = [
    {"sku": "SKU-00173", "sales_value_myr": 700638.47},
    {"sku": "SKU-00241", "sales_value_myr": 594317.77},
    {"sku": "WH-02", "sales_value_myr": 520762.68},
]


def _answer(**kw: Any) -> Answer:
    base: dict[str, Any] = {
        "answer": "Top SKUs by sales.",
        "sql_used": SQL,
        "audit_id": "audit-1",
        "route": "sql",
        "row_count": len(ROWS),
        "rows": ROWS,
        "provenance": Provenance(layer="governed_metric", badge=Badge.GOVERNED_METRIC),
    }
    base.update(kw)
    return Answer(**base)


def _writer(text: str, facts: list[InsightFact] | None = None):
    """Stand-in for a model-backed writer: returns fixed text, never calls a model."""

    def write(rows: list[dict[str, Any]], columns: list[str]):
        return text, list(facts or [])

    return write


# --- must-fail 1: a number not derivable from the rows is rejected ------------


@pytest.mark.parametrize(
    "text",
    [
        "Sales grew 12% this quarter.",
        "Highest sales_value_myr: 700,639 (sku SKU-00173).",
        "SKU-00173 sold about 1.8 million.",
        "4 rows returned.",
        "Lowest sales_value_myr: -520,762.68.",
    ],
)
def test_must_fail_insight_number_not_in_rows_is_rejected(text: str) -> None:
    with pytest.raises(rp.PackageRefused) as exc:
        rp.validate_insight(text, [], ROWS)
    assert exc.value.code == rp.INSIGHT_NUMBER_NOT_IN_ROWS

    package = rp.package_result(ROWS, SQL, insight_writer=_writer(text))
    assert package.insight.text is None
    assert package.insight.facts == []
    assert package.insight.served_reason == rp.INSIGHT_NUMBER_NOT_IN_ROWS
    assert package.insight.served_method == "writer"


def test_insight_numbers_that_are_in_the_rows_pass() -> None:
    text = (
        "3 rows returned. SKU-00173 leads at 700,638.47; WH-02 is last at 520,762.68 "
        "(about 520,763)."
    )
    rp.validate_insight(text, [], ROWS)
    package = rp.package_result(ROWS, SQL, insight_writer=_writer(text))
    assert package.insight.text == text and package.insight.served_reason is None


def test_label_digits_are_not_read_as_claims_but_bare_copies_are() -> None:
    assert rp.untraceable_numbers("Lowest is WH-02.", ROWS) == []
    assert rp.untraceable_numbers("Lowest is WH-07.", ROWS) == ["07"]
    assert rp.untraceable_numbers("SKU-00173 then 173 units", ROWS) == ["173"]


def test_insight_fact_that_does_not_recompute_is_rejected() -> None:
    lie = InsightFact(op="max", value=594317.77, column="sales_value_myr", row_index=1)
    with pytest.raises(rp.PackageRefused) as exc:
        rp.validate_insight("3 rows returned.", [lie], ROWS)
    assert exc.value.code == rp.INSIGHT_FACT_MISMATCH


def test_template_insight_numbers_all_trace_to_rows() -> None:
    package = rp.package_result(ROWS, SQL)
    insight = package.insight
    assert insight.served_reason is None and insight.served_method == rp.INSIGHT_METHOD
    assert insight.text == (
        "3 rows returned. Highest sales_value_myr: 700,638.47 (sku SKU-00173). "
        "Lowest sales_value_myr: 520,762.68 (sku WH-02)."
    )
    assert rp.untraceable_numbers(insight.text, ROWS) == []
    assert [(f.op, f.value, f.column, f.row_index) for f in insight.facts] == [
        ("row_count", 3.0, None, None),
        ("max", 700638.47, "sales_value_myr", 0),
        ("min", 520762.68, "sales_value_myr", 2),
    ]


@pytest.mark.parametrize(
    ("rows", "expected"),
    [
        ([], "The query returned 0 rows."),
        ([{"revenue_myr": 80787598.3}], "1 row returned: revenue_myr = 80,787,598.3."),
        ([{"carrier": "DHL"}, {"carrier": "UPS"}], "2 rows returned with columns carrier."),
        (
            [{"month": "2026-01", "units": 7}, {"month": "2026-02", "units": 12}],
            "2 rows returned. Highest units: 12 (month 2026-02). "
            "Lowest units: 7 (month 2026-01).",
        ),
    ],
)
def test_template_insight_shapes(rows: list[dict[str, Any]], expected: str) -> None:
    insight = rp.package_result(rows, SQL).insight
    assert insight.text == expected
    assert insight.served_reason is None
    assert rp.untraceable_numbers(expected, rows) == []


def test_a_failing_writer_never_breaks_the_package() -> None:
    def boom(rows, columns):  # noqa: ANN001, ANN202
        raise TimeoutError("writer timed out")

    package = rp.package_result(ROWS, SQL, insight_writer=boom)
    assert package.insight.text is None
    assert package.insight.served_reason == f"{rp.INSIGHT_WRITER_ERROR}:TimeoutError"
    assert package.chart_spec.spec is not None


# --- must-fail 2: a chart spec naming a column not in the rows is rejected ---


def _spec(**encoding: Any) -> dict[str, Any]:
    return {
        "$schema": rp.VEGA_LITE_SCHEMA,
        "data": {"name": "rows"},
        "mark": "bar",
        "encoding": encoding,
    }


@pytest.mark.parametrize(
    "spec",
    [
        _spec(x={"field": "sku", "type": "nominal"}, y={"field": "profit_myr", "type": "quantitative"}),
        _spec(x={"field": "warehouse", "type": "nominal"}, y={"field": "sales_value_myr"}),
        _spec(
            x={"field": "sku", "type": "nominal", "sort": {"field": "margin"}},
            y={"field": "sales_value_myr", "type": "quantitative"},
        ),
    ],
)
def test_must_fail_chart_spec_column_not_in_rows_is_rejected(
    spec: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    columns = ["sku", "sales_value_myr"]
    with pytest.raises(rp.PackageRefused) as exc:
        rp.validate_chart_spec(spec, columns)
    assert exc.value.code == rp.CHART_FIELD_NOT_IN_ROWS

    monkeypatch.setattr(rp, "build_chart_spec", lambda rows, cols: (spec, None))
    chart = rp.package_result(ROWS, SQL).chart_spec
    assert chart.spec is None and chart.fields == []
    assert chart.served_reason == rp.CHART_FIELD_NOT_IN_ROWS


@pytest.mark.parametrize(
    "spec",
    [
        {**_spec(x={"field": "sku"}), "data": {"values": [{"sku": "x", "sales_value_myr": 9}]}},
        {**_spec(x={"field": "sku"}), "transform": [{"calculate": "1", "as": "sku"}]},
        {**_spec(x={"field": "sku"}), "mark": "image"},
        _spec(x={"field": "sku", "sort": ["SKU-1", "SKU-2"]}),
        _spec(x={"field": "sku", "scale": {"domain": [0, 1]}}),
        _spec(),
    ],
)
def test_chart_spec_may_not_embed_values_or_escape_the_allowlist(spec: dict[str, Any]) -> None:
    with pytest.raises(rp.PackageRefused) as exc:
        rp.validate_chart_spec(spec, ["sku", "sales_value_myr"])
    assert exc.value.code == rp.CHART_SPEC_NOT_ALLOWED


@pytest.mark.parametrize(
    ("rows", "mark", "fields"),
    [
        (ROWS, "bar", ["sku", "sales_value_myr"]),
        ([{"revenue_myr": 80787598.3}], "text", ["revenue_myr"]),
        ([{"day": "2026-01-01", "kg": 3}, {"day": "2026-01-02", "kg": 4}], "line", ["day", "kg"]),
        ([{"kg": 1, "cost": 2}, {"kg": 3, "cost": 5}], "point", ["kg", "cost"]),
    ],
)
def test_chart_spec_follows_result_shape_and_names_only_row_columns(
    rows: list[dict[str, Any]], mark: str, fields: list[str]
) -> None:
    chart = rp.package_result(rows, SQL).chart_spec
    assert chart.served_reason is None and chart.served_method == rp.CHART_METHOD
    assert chart.spec is not None and chart.spec["mark"] == mark
    assert chart.spec["data"] == {"name": "rows"}
    assert chart.fields == fields
    assert set(chart.fields) <= set(rows[0])


@pytest.mark.parametrize("rows", [[], [{"carrier": "DHL"}], [{"kg": 1}, {"kg": 2}]])
def test_no_chart_when_the_shape_has_none(rows: list[dict[str, Any]]) -> None:
    chart = rp.package_result(rows, SQL).chart_spec
    assert chart.spec is None and chart.served_reason == rp.NO_CHARTABLE_SHAPE


# --- must-fail 3: packaging never runs on an abstain ------------------------


@pytest.mark.parametrize(
    "abstain",
    [
        {"provenance": Provenance(layer="abstain", badge=Badge.ABSTAIN,
                                  assumptions="sum does not reconcile")},
        {"provenance": Provenance(layer="blocked", badge=Badge.BLOCKED,
                                  assumptions="policy")},
        {"route": "needs_clarification",
         "provenance": Provenance(layer="needs_clarification", badge=Badge.SESSION)},
        {"route": "refused",
         "provenance": Provenance(layer="refused", badge=Badge.SESSION)},
    ],
)
def test_must_fail_abstain_is_never_packaged(
    abstain: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    # An abstain that still carries rows and SQL (for example an implausible
    # result) is exactly the case packaging must not dress up as an answer.
    packaged: list[str] = []
    real = rp.package_result
    monkeypatch.setattr(
        rp, "package_result", lambda *a, **k: packaged.append("ran") or real(*a, **k)
    )
    before = _answer(answer="Abstained: the sum does not reconcile.", **abstain)

    after = rp.package_answer(before)

    assert packaged == [], "packaging ran on an abstain"
    assert after.chart_spec is None and after.insight is None
    assert after.model_dump() == before.model_dump()
    assert after.answer == "Abstained: the sum does not reconcile."
    assert after.provenance == abstain["provenance"]
    assert "chart_spec" not in after.model_dump_json()


def test_answered_result_is_packaged_and_only_gains_the_package() -> None:
    before = _answer()
    after = rp.package_answer(before)
    assert after.chart_spec is not None and after.insight is not None
    assert after.insight.text and after.chart_spec.spec
    dumped = after.model_dump()
    for key in ("chart_spec", "insight"):
        dumped.pop(key)
    assert dumped == before.model_dump()


def test_answer_without_sql_is_not_packaged_and_package_result_needs_sql() -> None:
    assert rp.package_answer(_answer(sql_used=None)).chart_spec is None
    assert rp.package_answer(_answer(rows=None)).insight is None
    with pytest.raises(rp.PackageRefused) as exc:
        rp.package_result(ROWS, "  ")
    assert exc.value.code == rp.PACKAGE_WITHOUT_SQL


# --- served_* stamps, bounds, wire shape ------------------------------------


def test_every_artifact_is_stamped_with_the_rows_and_sql_it_came_from() -> None:
    package = rp.package_result(ROWS, SQL, served_at="2026-10-06T00:00:00+00:00")
    rows_sha = hashlib.sha256(
        json.dumps(ROWS, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    for artifact in (package.chart_spec, package.insight):
        assert artifact.served_by == rp.SERVED_BY
        assert artifact.served_at == "2026-10-06T00:00:00+00:00"
        assert artifact.served_rows_sha256 == rows_sha
        assert artifact.served_sql_sha256 == hashlib.sha256(SQL.encode()).hexdigest()
        assert artifact.served_row_count == 3
        assert artifact.served_rows_truncated is False
    assert package.sql == SQL and package.rows == ROWS


def test_rows_are_bounded_and_the_package_says_so() -> None:
    rows = [{"n": i} for i in range(rp.MAX_PACKAGE_ROWS + 5)]
    package = rp.package_result(rows, SQL)
    assert len(package.rows) == rp.MAX_PACKAGE_ROWS
    assert package.insight.served_rows_truncated is True
    assert package.insight.served_row_count == rp.MAX_PACKAGE_ROWS
    answer = rp.package_answer(_answer(rows=rows, row_count=len(rows)))
    assert answer.rows is not None and len(answer.rows) == rp.MAX_PACKAGE_ROWS


def test_unpackaged_answer_serialises_without_the_new_fields() -> None:
    plain = _answer()
    assert "chart_spec" not in plain.model_dump() and "insight" not in plain.model_dump()
    assert '"chart_spec"' not in plain.model_dump_json()
    packaged = rp.package_answer(plain)
    wire = json.loads(packaged.model_dump_json())
    assert wire["chart_spec"]["served_by"] == rp.SERVED_BY
    assert Answer.model_validate(wire).insight == packaged.insight


def test_answer_schema_publishes_the_package_fields() -> None:
    props = Answer.model_json_schema()["properties"]
    assert {"chart_spec", "insight"} <= set(props)
    assert "required" not in Answer.model_json_schema() or not (
        {"chart_spec", "insight"} & set(Answer.model_json_schema()["required"])
    )


def test_flag_defaults_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(rp.ENABLED_ENV, raising=False)
    assert rp.result_package_enabled() is False
    for raw in ("0", "false", "off", ""):
        monkeypatch.setenv(rp.ENABLED_ENV, raw)
        assert rp.result_package_enabled() is False
    monkeypatch.setenv(rp.ENABLED_ENV, "1")
    assert rp.result_package_enabled() is True


def test_packaging_module_imports_no_model_db_or_network() -> None:
    tree = ast.parse((ROOT / "CortexOS" / "dms" / "result_package.py").read_text("utf-8"))
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            roots.add(node.module.split(".")[0])
    stdlib = set(sys.stdlib_module_names) | {"__future__"}
    assert roots - stdlib == {"cortex_contract"}
