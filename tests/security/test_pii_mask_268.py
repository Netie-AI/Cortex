"""Cortex #268 PII-MASK-FREEROUTE: no model call leaves the process with PII.

Every case captures the outbound body at the transport (the scripted OpenVault
from tests/conftest.py, or a fake litellm) and asserts none of the seeded
values are in it. No network, no real key, no NER model.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest

from CortexOS.dms import l2_generation
from CortexOS.integrations import freeroute, pii_mask
from CortexOS.security import redact_port

IC = "900101-14-5678"
PHONE = "012-345 6789"
EMAIL = "procurement1@supplier001.example.com"
ACCOUNT = "12345678901"
NAME = "Encik Tan Ah Kow"
SEEDED = (IC, PHONE, EMAIL, ACCOUNT, NAME, "Tan Ah Kow", "procurement1@")

PII_QUESTION = (
    f"which supplier uses the email {EMAIL}? asked by {NAME}, IC {IC}, "
    f"phone {PHONE}, account {ACCOUNT}"
)


def _assert_no_pii(payload: Any) -> None:
    raw = json.dumps(payload, default=str)
    for value in SEEDED:
        assert value not in raw, f"{value!r} left the process: {raw[:400]}"


@pytest.fixture(autouse=True)
def _no_detector():
    pii_mask.clear_span_detector()
    redact_port.clear_redactor()
    yield
    pii_mask.clear_span_detector()
    redact_port.clear_redactor()


# -- engine gen-ask-sql (answer path) ---------------------------------------------


@pytest.fixture()
def _l2_real_port(monkeypatch):
    from bench.accuracy import _ensure_db_loaded
    from packs.dms.generative.l2_adapter import DmsL2Generation

    _ensure_db_loaded()
    monkeypatch.setenv("DMS_L2_ENABLED", "1")
    monkeypatch.delenv("DMS_L2_SHADOW", raising=False)
    port = DmsL2Generation()
    monkeypatch.setattr(l2_generation, "resolve_l2_generation", lambda: port)
    monkeypatch.setattr("CortexOS.dms.answer_engine.match_certified", lambda q: None)
    monkeypatch.setattr("CortexOS.dms.answer_engine.route_to_metric", lambda q: None)
    monkeypatch.setattr("CortexOS.dms.answer_engine.undefined_subject", lambda q: None)
    monkeypatch.setattr("CortexOS.dms.answer_engine._shape_refusal", lambda q: None)
    monkeypatch.setattr("packs.dms.semantic.query_skills.find", lambda *a, **k: None)
    monkeypatch.setattr("packs.dms.semantic.catalog_answer.is_catalog_intent", lambda q: False)


def test_gen_ask_sql_masks_pii_and_the_masked_literal_binds_the_stored_row(
    armed_openvault, _l2_real_port
) -> None:
    from CortexOS.dms.answer_engine import answer

    # The model only ever sees the placeholder, so that is what it writes back.
    armed_openvault.reply("SELECT supplier_name, email FROM suppliers WHERE email = '<PII:EMAIL_1>'")
    env = answer(PII_QUESTION)

    assert len(armed_openvault.chat_calls) == 1
    body = armed_openvault.chat_calls[0]["body"]
    _assert_no_pii(body)
    assert "<PII:EMAIL_1>" in json.dumps(body)
    assert "<PII:IC_1>" in json.dumps(body)

    # Restored locally before validation: the SQL ran against the real value.
    assert env["badge"] == "L2_VALIDATED", env
    rows = env.get("rows") or []
    assert rows == [{"supplier_name": "Malaysia Supplier 001 Sdn Bhd", "email": EMAIL}]
    assert "Malaysia Supplier 001 Sdn Bhd" in (env.get("answer") or "")
    assert EMAIL in (env.get("sql_used") or "")
    assert "<PII:" not in (env.get("sql_used") or "")


# -- CoT think + generative_ask (Insights) ------------------------------------------


@pytest.mark.asyncio
async def test_insights_think_and_generative_ask_are_masked(armed_openvault, monkeypatch) -> None:
    from CortexOS.crew import insights
    from tests.test_crew.test_insights import ScriptedBridge, _certified_engine

    tasks: list[str] = []
    real = freeroute.complete

    def recording(task, messages, **kwargs):  # noqa: ANN001
        tasks.append(task)
        return real(task, messages, **kwargs)

    monkeypatch.setattr(freeroute, "complete", recording)
    armed_openvault.default_content = "SELECT COUNT(DISTINCT sku) AS sku_count FROM inventory"
    env = await insights.run_insights(
        f"how many skus does {NAME} (IC {IC}, {EMAIL}, {PHONE}, acct {ACCOUNT}) hold",
        bridge=ScriptedBridge(_certified_engine()),
        ask=False,
        generate=True,
    )
    assert armed_openvault.chat_calls, env
    # think, then generative_ask (crew task names from crew/freeroute.task_for).
    assert tasks[:2] == ["crew-think", "crew-insights-sql"], tasks
    for call in armed_openvault.chat_calls:
        _assert_no_pii(call["body"])
    # The envelope the caller gets never carries a placeholder map.
    assert "restore" not in json.dumps(env, default=str)


# -- Crew act with a tool result -----------------------------------------------------


@pytest.mark.asyncio
async def test_crew_act_with_a_tool_result_is_masked(armed_openvault) -> None:
    from CortexOS.crew import freeroute as crew_fr

    armed_openvault.reply("Emailed them.")
    messages = [
        {"role": "user", "content": "look up the supplier contact"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "lookup", "arguments": json.dumps({"who": NAME})},
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "call_1",
            "name": "lookup",
            "content": json.dumps({"email": EMAIL, "phone": PHONE, "ic": IC, "acct": ACCOUNT}),
        },
    ]
    out = await crew_fr.complete(messages, purpose="act", tools=[])
    assert len(armed_openvault.chat_calls) == 1, out
    sent = armed_openvault.chat_calls[0]["body"]["messages"]
    _assert_no_pii(sent)
    # Protocol fields are untouched.
    assert sent[-1]["tool_call_id"] == "call_1" and sent[-1]["name"] == "lookup"
    assistant = next(m for m in sent if m.get("tool_calls"))
    assert assistant["tool_calls"][0]["function"]["name"] == "lookup"
    assert "<PII:NAME_1>" in assistant["tool_calls"][0]["function"]["arguments"]


# -- core stamp, restore, fail closed, guard ------------------------------------------


def test_stamp_records_counts_by_kind_never_values_and_reply_is_restored(armed_openvault) -> None:
    armed_openvault.reply("Found <PII:EMAIL_1> for <PII:NAME_1>")
    seen: list[str] = []

    def accept(text: str) -> bool:
        seen.append(text)
        return True

    with freeroute.journal() as stamps:
        out = freeroute.complete("crew-think", [{"role": "user", "content": PII_QUESTION}], accept=accept)
    assert out.ok, out.reason
    _assert_no_pii(armed_openvault.chat_calls[0]["body"])
    assert out.text == f"Found {EMAIL} for {NAME}"
    assert seen == [out.text]  # validators see restored text
    stamp = stamps[-1]
    assert stamp.masked == {"EMAIL": 1, "NAME": 1, "IC": 1, "PHONE": 1, "ACCOUNT": 1}
    _assert_no_pii(stamp.public())
    assert freeroute.router_fingerprint(stamp)["masking_state"] == "on"


@pytest.mark.parametrize("where", ["span_detector", "redact_port"])
def test_masker_error_refuses_and_sends_nothing(armed_openvault, where) -> None:
    def boom(*_a: Any) -> Any:
        raise RuntimeError("ner model failed to load")

    if where == "span_detector":
        pii_mask.register_span_detector(boom)
    else:
        redact_port.register_redactor(boom)
    with freeroute.journal() as stamps:
        out = freeroute.complete("crew-think", [{"role": "user", "content": "hello"}])
    assert out.ok is False
    assert pii_mask.REFUSED_REASON in out.reason
    assert armed_openvault.chat_calls == []
    assert stamps and stamps[-1].status is None
    assert pii_mask.REFUSED_REASON in stamps[-1].line()


def test_guard_every_transport_call_carries_the_masker_output(armed_openvault, monkeypatch) -> None:
    """Fails on any complete() that reaches the transport without the masker."""
    sentinel = [{"role": "user", "content": "masked-by-pii_mask"}]
    calls: list[Any] = []

    def spy(messages):  # noqa: ANN001
        calls.append(messages)
        return pii_mask.Masked(messages=sentinel)

    monkeypatch.setattr(pii_mask, "mask_messages", spy)
    for task in ("gen-ask-sql", "crew-think", "crew-act", "crew-prompt", "insights"):
        freeroute.complete(task, [{"role": "user", "content": f"raw {IC}"}])
    assert len(calls) == 5
    assert len(armed_openvault.chat_calls) == 5
    for call in armed_openvault.chat_calls:
        assert call["body"]["messages"] == sentinel


# -- Crew litellm path (env provider keys) --------------------------------------------


class _FakeLitellm:
    telemetry = True
    drop_params = False
    suppress_debug_info = False

    def __init__(self, content: str, args: str = "{}") -> None:
        self.sent: list[dict[str, Any]] = []
        self.content = content
        self.args = args

    async def acompletion(self, **kwargs: Any) -> Any:
        self.sent.append(kwargs)
        call = SimpleNamespace(
            id="c1", function=SimpleNamespace(name="send_email", arguments=self.args)
        )
        message = SimpleNamespace(content=self.content, tool_calls=[call], reasoning_content=None)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=message, finish_reason="stop")], usage=None
        )

    def completion_cost(self, **_: Any) -> float:
        return 0.0


@pytest.mark.asyncio
async def test_crew_litellm_chat_masks_and_restores(monkeypatch) -> None:
    from CortexOS.crew import llm

    fake = _FakeLitellm("Mailed <PII:EMAIL_1>", args=json.dumps({"to": "<PII:EMAIL_1>"}))
    monkeypatch.setattr(llm, "_litellm", lambda: fake)
    result = await llm.chat(
        "gemini/gemini-3-flash-preview",
        [
            {"role": "user", "content": PII_QUESTION},
            {"role": "tool", "tool_call_id": "t1", "content": json.dumps({"phone": PHONE})},
        ],
    )
    assert len(fake.sent) == 1
    _assert_no_pii(fake.sent[0]["messages"])
    assert result.text == f"Mailed {EMAIL}"
    assert result.tool_calls[0].args == {"to": EMAIL}


@pytest.mark.asyncio
async def test_crew_litellm_masker_error_raises_before_any_send(monkeypatch) -> None:
    from CortexOS.crew import llm

    fake = _FakeLitellm("never")
    monkeypatch.setattr(llm, "_litellm", lambda: fake)
    pii_mask.register_span_detector(lambda _t: (_ for _ in ()).throw(RuntimeError("down")))
    with pytest.raises(llm.LLMError, match=pii_mask.REFUSED_REASON):
        await llm.chat("gemini/gemini-3-flash-preview", [{"role": "user", "content": "hi"}])
    assert fake.sent == []


# -- the masker itself ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "kind"),
    [
        ("IC 900101145678 here", "IC"),
        ("NRIC S1234567D", "IC"),
        ("call +60 12-345 6789", "PHONE"),
        ("call +6012 3456789", "PHONE"),
        ("call +44 20 7946 0958", "PHONE"),
        ("mail a.b@c.co", "EMAIL"),
        ("acct 1234567890", "ACCOUNT"),
        ("card 4111 1111 1111 1111", "CARD"),
        ("from Puan Siti Aminah today", "NAME"),
        ("from Ahmad bin Ismail today", "NAME"),
        ("from Muthu a/l Rajan today", "NAME"),
    ],
)
def test_masker_kinds(text: str, kind: str) -> None:
    masked = pii_mask.mask_messages([{"role": "user", "content": text}])
    assert masked.counts == {kind: 1}, masked
    assert f"<PII:{kind}_1>" in masked.messages[0]["content"]
    assert pii_mask.restore_text(masked.messages[0]["content"], masked.restore) == text


def test_masker_leaves_analytics_text_alone_and_is_stable() -> None:
    text = "top 10 skus by quantity_kg in WH-A for 2026-06, SKU-90001 LIMIT 50"
    masked = pii_mask.mask_messages([{"role": "user", "content": text}])
    assert masked.counts == {} and masked.messages[0]["content"] == text
    twice = pii_mask.mask_messages([{"role": "user", "content": f"{EMAIL} and {EMAIL}"}])
    assert twice.messages[0]["content"] == "<PII:EMAIL_1> and <PII:EMAIL_1>"
    assert twice.counts == {"EMAIL": 2}
