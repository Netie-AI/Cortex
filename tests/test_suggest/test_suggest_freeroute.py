"""SUGGEST (#308) model path: OpenVault FreeRoute only, mocked in CI.

Every case runs the real ``freeroute.complete`` against the scripted OpenVault
in tests/freeroute_fake.py (``fake_openvault`` / ``armed_openvault`` in
tests/conftest.py); no socket is opened and no live model is called. The model
only ever sees masked templates, and a rewording that widens a follow-up is
refused and replaced by its template.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from CortexOS.integrations import freeroute
from CortexOS.suggest import ask as suggest_ask
from CortexOS.suggest import followups as fu
from CortexOS.suggest import freeroute_rephrase as fr_rephrase
from CortexOS.suggest.followups import Result, Schema, Table

SCHEMA = Schema(
    tables={
        "orders": Table(
            "orders",
            ("order_id", "region", "status", "amount", "placed_at"),
            {"order_id": "string", "region": "string", "status": "string", "amount": "number", "placed_at": "date"},
            primary_key="order_id",
        ),
        "payroll": Table("payroll", ("salary",), {"salary": "number"}),
    },
)
RESULT = Result(
    question="revenue by region",
    rows=({"region": "North", "revenue": 120.5}, {"region": "South", "revenue": 80.0}),
    tables=("orders",),
    granted=frozenset({"orders"}),
)
SERVED = {"served_provider": "groq", "served_model": "openai/gpt-oss-120b", "served_local": False}


def _reply(fake, texts: list[str] | str) -> None:
    content = texts if isinstance(texts, str) else json.dumps(texts)
    fake.replies.append(
        (200, {"model": "openai/gpt-oss-120b", "choices": [{"message": {"role": "assistant", "content": content}}], **SERVED})
    )


def _suggest_calls(fake) -> list[dict[str, Any]]:
    return [
        c for c in fake.chat_calls
        if c["body"]["messages"][0]["content"] == fr_rephrase._SYSTEM
    ]


def _templates() -> list[str]:
    return [fu._mask(c) for c in fu.propose(RESULT, SCHEMA)[: fu.MAX_FOLLOWUPS]]


def test_rewording_goes_through_openvault_freeroute_and_is_stamped(armed_openvault) -> None:
    templates = _templates()
    _reply(armed_openvault, [t.replace("Show the", "List the") for t in templates])
    outcome = fu.suggest(RESULT, SCHEMA, rephrase=fr_rephrase.freeroute_rephrase)

    (call,) = _suggest_calls(armed_openvault)
    assert call["path"] == "/v1/chat/completions"
    assert call["base"].rstrip("/") == "http://127.0.0.1:5000"
    assert armed_openvault.non_openvault_calls == []
    first = outcome.followups[0]
    assert first["question"] == "List the orders rows where region is 'North'."
    assert first["served_by"] == freeroute.IMPL
    assert first["served_provider"] == "groq"
    assert first["served_model"] == "openai/gpt-oss-120b"
    assert first["served_local"] is False
    assert "rephrased (FreeRoute call " in first["served_reason"]


def test_the_model_never_sees_a_result_value(armed_openvault) -> None:
    _reply(armed_openvault, _templates())
    fu.suggest(RESULT, SCHEMA, rephrase=fr_rephrase.freeroute_rephrase)
    (call,) = _suggest_calls(armed_openvault)
    sent = json.dumps(call["body"]["messages"])
    assert "{v1}" in sent
    for value in ("North", "South", "120.5", "80.0"):
        assert value not in sent


def test_unarmed_freeroute_serves_templates_and_makes_no_call(fake_openvault) -> None:
    outcome = fu.suggest(RESULT, SCHEMA, rephrase=fr_rephrase.freeroute_rephrase)
    assert fake_openvault.chat_calls == []
    assert outcome.followups and all(f["served_by"] == fu.SERVED_BY for f in outcome.followups)


@pytest.mark.parametrize("content", ["not json", json.dumps(["only one"]), json.dumps({"a": 1})])
def test_unusable_model_reply_serves_templates(armed_openvault, content: str) -> None:
    _reply(armed_openvault, content)
    outcome = fu.suggest(RESULT, SCHEMA, rephrase=fr_rephrase.freeroute_rephrase)
    assert outcome.followups == fu.suggest(RESULT, SCHEMA, now=outcome.followups[0]["served_at"]).followups


@pytest.mark.parametrize(
    "widen",
    [
        "Break down revenue by salary in payroll.",
        "Break down revenue by profit_margin in orders.",
        "Show the orders rows where region is 'West'.",
    ],
    ids=["ungranted_table", "invented_column", "invented_value"],
)
def test_must_fail_freeroute_rewording_that_widens_is_refused(armed_openvault, widen: str) -> None:
    _reply(armed_openvault, [widen for _ in _templates()])
    outcome = fu.suggest(RESULT, SCHEMA, rephrase=fr_rephrase.freeroute_rephrase)
    assert len(_suggest_calls(armed_openvault)) == 1
    assert outcome.followups
    for f in outcome.followups:
        assert f["question"] != widen
        assert f["served_by"] == fu.SERVED_BY


def test_must_fail_contract_ask_model_cannot_widen_followups(
    ask_http, armed_openvault, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(suggest_ask.ENABLED_ENV, "1")
    monkeypatch.setenv(fr_rephrase.MODEL_ENV, "1")
    ask_http.bind({"transactions": "TRUE"})
    templated = ask_http.ask("top 5 skus by revenue").json()
    n = len(templated["followups"])
    widen = "Break down sales_value_myr by supplier_name in suppliers."
    _reply(armed_openvault, [widen] * n)
    body = ask_http.ask("top 5 skus by revenue").json()

    assert body["rows"] == templated["rows"] and body["answer"] == templated["answer"]
    assert len(armed_openvault.chat_calls) == 2
    assert len(_suggest_calls(armed_openvault)) == 2
    assert len(body["followups"]) == n
    for f in body["followups"]:
        assert "suppliers" not in f["question"] and "supplier_name" not in f["question"]
        assert f["tables"] == ["transactions"]
        assert f["served_by"] == fu.SERVED_BY
