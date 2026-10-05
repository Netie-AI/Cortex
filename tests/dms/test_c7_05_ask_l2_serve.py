"""C7-05 part 2: ask-route L2 uses the Insights numeric gate class."""

from __future__ import annotations

from typing import Any

import pytest

from CortexOS.dms.l2_generation import L2Attempt
from tests.dms.test_c7_02_manifest_before_explain import _verified

QUESTION = "Give the governed inventory SKU total for a custom comparison"
SQL = "SELECT COUNT(DISTINCT sku) AS sku_count FROM inventory"


@pytest.fixture(autouse=True)
def _ask_l2_miss(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DMS_L2_ENABLED", "1")
    monkeypatch.setattr("CortexOS.dms.answer_engine.match_certified", lambda q: None)
    monkeypatch.setattr("CortexOS.dms.answer_engine.route_to_metric", lambda q: None)
    monkeypatch.setattr("CortexOS.dms.answer_engine.undefined_subject", lambda q: None)
    monkeypatch.setattr("CortexOS.dms.answer_engine._shape_refusal", lambda q: None)
    monkeypatch.setattr("packs.dms.semantic.query_skills.find", lambda *a, **k: None)
    monkeypatch.setattr(
        "packs.dms.semantic.catalog_answer.is_catalog_intent", lambda q: False
    )


def _ranking(*, certified: bool = False) -> dict[str, Any]:
    row = {"id": "cq_sku_count" if certified else "sku_count", "importance": {"score": 9}}
    return {
        "ok": True,
        "locations": [{"id": "inventory", "where": {"table": "inventory"}}],
        "metrics": [] if certified else [row],
        "certified": [row] if certified else [],
    }


def _attempt(sql: str = SQL) -> L2Attempt:
    return L2Attempt(
        sql=sql,
        assumptions="test L2 attempt",
        retrieved_tables=("inventory",),
        route_call="call-ask-l2",
    )


def test_ask_l2_serves_only_after_plausibility_columns_and_grain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from CortexOS.dms import answer_engine
    from CortexOS.dms.l2_plausibility import PlausibilityResult

    calls: list[str] = []
    monkeypatch.setattr(
        "CortexOS.dms.l2_generation.attempt_l2",
        lambda *a, **k: calls.append("manifest_explain") or _attempt(),
    )
    monkeypatch.setattr(
        "CortexOS.crew.insights.select_ranking",
        lambda *a, **k: _ranking(),
    )
    monkeypatch.setattr(
        "CortexOS.execution.submit.execute_sql",
        lambda *a, **k: calls.append("execute") or ([{"sku_count": 4}], 0.0, 0.0),
    )
    monkeypatch.setattr("CortexOS.execution.submit.execute_count", lambda *a, **k: 1)
    monkeypatch.setattr(
        "CortexOS.dms.l2_plausibility.assess_plausibility",
        lambda *a, **k: calls.append("plausibility") or PlausibilityResult(ok=True),
    )

    body = answer_engine.answer(QUESTION, verified=_verified({"inventory": "TRUE"}))

    assert calls == ["manifest_explain", "execute", "plausibility"]
    assert body["layer"] == "generated"
    assert body["badge"] == "L2_VALIDATED"
    assert body["rows"] == [{"sku_count": 4}]
    assert "4" in body["answer"]


def test_ask_l2_certified_measure_mismatch_abstains_before_execute(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from CortexOS.dms import answer_engine

    executed: list[str] = []
    monkeypatch.setattr(
        "CortexOS.dms.l2_generation.attempt_l2",
        lambda *a, **k: _attempt("SELECT COUNT(*) AS sku_count FROM inventory"),
    )
    monkeypatch.setattr(
        "CortexOS.crew.insights.select_ranking",
        lambda *a, **k: _ranking(certified=True),
    )
    monkeypatch.setattr(
        "CortexOS.execution.submit.execute_sql",
        lambda *a, **k: executed.append("execute"),
    )

    body = answer_engine.answer(QUESTION, verified=_verified({"inventory": "TRUE"}))

    assert executed == []
    assert body["layer"] == "abstain"
    assert body["badge"] == "abstain"
    assert body["rows"] == []
    assert body["sql_used"] is None
    assert "certified_measure_not_used:cq_sku_count" in body["answer"]


def test_ask_l2_wrong_result_columns_abstain_after_plausibility(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from CortexOS.dms import answer_engine
    from CortexOS.dms.l2_plausibility import PlausibilityResult

    calls: list[str] = []
    monkeypatch.setattr(
        "CortexOS.dms.l2_generation.attempt_l2", lambda *a, **k: _attempt()
    )
    monkeypatch.setattr(
        "CortexOS.crew.insights.select_ranking", lambda *a, **k: _ranking()
    )
    monkeypatch.setattr(
        "CortexOS.execution.submit.execute_sql",
        lambda *a, **k: ([{"wrong_count": 4}], 0.0, 0.0),
    )
    monkeypatch.setattr("CortexOS.execution.submit.execute_count", lambda *a, **k: 1)
    monkeypatch.setattr(
        "CortexOS.dms.l2_plausibility.assess_plausibility",
        lambda *a, **k: calls.append("plausibility") or PlausibilityResult(ok=True),
    )

    body = answer_engine.answer(QUESTION, verified=_verified({"inventory": "TRUE"}))

    assert calls == ["plausibility"]
    assert body["layer"] == "abstain"
    assert body["badge"] == "abstain"
    assert body["rows"] == []
    assert body["sql_used"] is None
    assert "expected columns=['sku_count']" in body["answer"]
    assert "row_columns=['wrong_count']" in body["answer"]
