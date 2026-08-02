"""EPIC-013 INS-01/INS-02: grounded insights + schema chart_spec.

Assert customer-visible answer text and chart_spec shapes. Numbers in insight
bullets must already appear in ``rows`` — never invent a metric.
"""

from __future__ import annotations

import re

from CortexOS.dms.answer_engine import clear_session
from CortexOS.dms.query_service import (
    answer_question,
    build_chart_spec,
    build_grounded_insights,
    format_answer_with_insights,
    synthesize_answer,
)


def _allowed_number_tokens(rows: list[dict]) -> set[str]:
    """Every number an insight bullet may cite (row values + row count)."""
    allowed = {str(len(rows))}
    for r in rows:
        for v in r.values():
            if v is None or isinstance(v, bool):
                continue
            allowed.add(str(v))
            try:
                f = float(v)
            except (TypeError, ValueError):
                continue
            allowed.add(str(f))
            if f == int(f):
                allowed.add(str(int(f)))
    return allowed


def _assert_no_invented_stats(answer: str, rows: list[dict]) -> None:
    if "Insights:" not in answer:
        return
    block = answer.split("Insights:", 1)[1]
    allowed = _allowed_number_tokens(rows)
    for num in re.findall(r"-?\d+(?:\.\d+)?", block):
        if num in allowed:
            continue
        # Digits already inside a row cell (e.g. SKU-00397) are not invented.
        digit_run = num.lstrip("-")
        if digit_run and any(digit_run in token for token in allowed):
            continue
        assert False, f"insight cites invented number {num!r} not in rows"


def test_multi_row_gets_grounded_bullets() -> None:
    rows = [
        {"sku": "SKU-A", "sales_value_myr": 100},
        {"sku": "SKU-B", "sales_value_myr": 500},
        {"sku": "SKU-C", "sales_value_myr": 200},
    ]
    base = synthesize_answer(rows, "top sales by value")
    text = format_answer_with_insights(base, rows)
    assert "Insights:" in text
    assert "SKU-B" in text
    assert "500" in text
    _assert_no_invented_stats(text, rows)


def test_empty_rows_get_no_insights() -> None:
    assert build_grounded_insights([]) == []
    text = format_answer_with_insights("No rows matched your query.", [])
    assert "Insights:" not in text


def test_invented_stat_detector_fails_on_foreign_number() -> None:
    rows = [{"sku": "A", "qty": 10}, {"sku": "B", "qty": 20}]
    fake = "Insights:\n- Highest qty: 999 (A)."
    try:
        _assert_no_invented_stats(fake, rows)
    except AssertionError:
        return
    raise AssertionError("detector must reject numbers absent from rows")


def test_ask_path_appends_grounded_insights_on_envelope() -> None:
    """INS-01 acceptance: customer-visible ``answer`` carries row-grounded bullets."""
    sid = "ins01-ask-envelope"
    clear_session(sid)
    result = answer_question("Top 5 selling SKUs by revenue", session_id=sid)
    assert result["badge"] in ("certified", "governed_metric", "query_skill", "L2_VALIDATED")
    assert result["rows"]
    assert "Insights:" in result["answer"]
    _assert_no_invented_stats(result["answer"], result["rows"])


def test_chart_scalar_bignum() -> None:
    spec = build_chart_spec([{"revenue_myr": 42}], "total revenue")
    assert spec is not None
    assert spec["type"] == "bignum"
    assert spec["value"] == 42


def test_chart_category_measure_bar() -> None:
    rows = [
        {"category": "Electronics", "qty": 12},
        {"category": "Food", "qty": 5},
    ]
    spec = build_chart_spec(rows, "stock by category")
    assert spec is not None
    assert spec["type"] == "bar"
    assert spec["x_label"] == "category"
    assert spec["y_label"] == "qty"
    assert len(spec["data"]) == 2
    assert {d["name"] for d in spec["data"]} == {"Electronics", "Food"}


def test_chart_time_series_line() -> None:
    rows = [
        {"order_date": "2024-01-01", "qty": 1},
        {"order_date": "2024-02-01", "qty": 3},
    ]
    spec = build_chart_spec(rows, "monthly volume")
    assert spec is not None
    assert spec["type"] == "line"
    assert spec["x_label"] == "order_date"
    assert all(isinstance(d["value"], float) for d in spec["data"])


def test_chart_text_only_is_null() -> None:
    rows = [{"sku": "A", "note": "ok"}, {"sku": "B", "note": "bad"}]
    assert build_chart_spec(rows, "list notes") is None
