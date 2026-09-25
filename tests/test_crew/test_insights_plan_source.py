"""Cortex #211 NARROW: honest plan_source on Insights generative-ask.

Studio (DMS #231) reads payload.plan_source as a producer stamp. Missing or
unknown is other. ontology_plan only when SQL came from NL -> ontology plan
-> FreeRoute SQL -> validate. Request mode=ontology_plan is not a stamp.

The engine-certified test must fail if decide_plan_source always returns
ontology_plan (relabel-everything shortcut).
"""

from __future__ import annotations

from typing import Any

import pytest

from CortexOS.crew import insights
from tests.test_crew.test_insights import ScriptedBridge, _certified_engine, _replies


def _armed(monkeypatch: pytest.MonkeyPatch) -> None:
    from CortexOS.crew import freeroute as fr

    monkeypatch.setattr(
        fr,
        "arming",
        lambda: {
            "ok": True,
            "armed": True,
            "detail": "vault-armed",
            "live_5000_ci": False,
        },
    )


@pytest.mark.asyncio
async def test_ontology_plan_generate_sql_is_labelled_ontology_plan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Real generate path: ontology ranking then validated SQL."""
    _armed(monkeypatch)
    prompts: list[str] = []

    async def fake_complete(messages=None, *, purpose="", prompt="", **kwargs):  # noqa: ANN001
        _ = messages, kwargs
        if purpose == "generative_ask":
            prompts.append(prompt or "")
            assert "inventory" in (prompt or "").lower()
            assert "ONTOLOGY PLAN measure=sku_count" in (prompt or "")
        return {
            "ok": True,
            "text": "SELECT COUNT(DISTINCT sku) AS sku_count FROM inventory",
            "identity": "cortex:crew:generative-ask",
            "route": {"label": "deepseek", "model": "deepseek-chat"},
        }

    bridge = ScriptedBridge(_certified_engine())
    env = await insights.run_insights(
        "how many skus",
        bridge=bridge,
        ask=False,
        generate=True,
        complete=fake_complete,
        query_plan={"measure": "sku_count", "group_by": [], "filters": []},
    )
    assert env["status"] == "ABSTAIN"
    assert env["values"] == []
    assert env["plan_source"] == insights.PLAN_SOURCE_ONTOLOGY
    assert env["query_sql"]
    assert "from inventory" in env["query_sql"].lower()
    assert "sku_count" in (env["query_sql"] or "").lower() or "count" in (
        env["query_sql"] or ""
    ).lower()
    assert env["generative"]["ok"] is True
    assert env["generative"]["valid"] is True
    assert env["generative"]["plan_source"] == insights.PLAN_SOURCE_ONTOLOGY
    assert bridge.asked == []
    assert prompts
    text = insights.render_tool_text(env)
    assert "plan_source: ontology_plan" in text
    assert "status: ABSTAIN" in text
    assert "12" not in env["answer"]


@pytest.mark.asyncio
async def test_engine_certified_ask_is_not_labelled_ontology_plan() -> None:
    """Non-plan answer. Relabel-everything (always ontology_plan) must fail here."""
    bridge = ScriptedBridge(_certified_engine())
    env = await insights.run_insights("how many skus", bridge=bridge, ask=True)
    assert env["status"] == "CERTIFIED"
    assert env["values"] == [{"sku_count": 12}]
    assert "12" in (env.get("answer") or "")
    assert env["plan_source"] == insights.PLAN_SOURCE_OTHER
    assert env["plan_source"] != insights.PLAN_SOURCE_ONTOLOGY
    assert "query_sql" not in env
    text = insights.render_tool_text(env)
    assert "12" in text
    assert "plan_source: other" in text
    assert "plan_source: ontology_plan" not in text
    assert insights.decide_plan_source(generated_sql=None, engine_answer=True) != (
        insights.PLAN_SOURCE_ONTOLOGY
    )
    assert (
        insights.decide_plan_source(
            generated_sql="SELECT 1 FROM inventory", engine_answer=True
        )
        != insights.PLAN_SOURCE_ONTOLOGY
    )


@pytest.mark.asyncio
async def test_request_mode_ontology_plan_does_not_relabel_unarmed(
    crew_env: Any,
) -> None:
    """DMS compute_query always sends mode=ontology_plan. That is not a stamp."""
    _ = crew_env
    bridge = ScriptedBridge(_certified_engine())
    env = await insights.run_insights(
        "how many skus",
        bridge=bridge,
        ask=False,
        generate=True,
    )
    assert env["status"] == "REFUSE"
    assert env["values"] == []
    assert env["plan_source"] != insights.PLAN_SOURCE_ONTOLOGY
    assert env["plan_source"] == insights.PLAN_SOURCE_OTHER
    assert "query_sql" not in env
    # Caller mode is not an argument of decide_plan_source.
    assert "mode" not in insights.decide_plan_source.__code__.co_varnames


@pytest.mark.asyncio
async def test_off_ontology_sql_is_not_labelled_ontology_plan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _armed(monkeypatch)
    env = await insights.run_insights(
        "how many skus",
        bridge=ScriptedBridge(_certified_engine()),
        ask=False,
        generate=True,
        complete=_replies("SELECT secret FROM payroll"),
    )
    assert env["status"] == "REFUSE"
    assert env["values"] == []
    assert env["plan_source"] != insights.PLAN_SOURCE_ONTOLOGY
    assert "query_sql" not in env
    text = insights.render_tool_text(env)
    assert "plan_source: ontology_plan" not in text
    assert "999" not in text
