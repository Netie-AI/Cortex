"""FREEROUTE-HONOR-01 (Refs #211): score a requested id by its own honored calls.

On main bcdbb45 ``_stats`` credited a requested id with the validity of the model
that served it. OpenVault's groq hop answers a request for
``thinkingmachines/inkling:free`` with ``openai/gpt-oss-120b`` in non-strict mode,
so inkling inherited gpt-oss's score and was picked. With ``CORTEX_OV_STRICT=1``
OpenVault refused that pin (503 ``pin_unavailable``), the refusal was unscored,
and Insights answered ABSTAIN.

The fake OpenVault below is the scripted one from tests/freeroute_fake.py with
the chat route replaced: it substitutes gpt-oss for an inkling request when the
body has no ``strict``, and refuses that pin with 503 ``pin_unavailable`` when it
has ``strict: true``. gpt-oss is always honored.
"""

from __future__ import annotations

import time
from typing import Any

import pytest

from CortexOS.crew import insights
from CortexOS.integrations import freeroute as fr
from CortexOS.integrations import openvault_client
from tests.test_crew.test_insights import ScriptedBridge, _certified_engine

INKLING = "thinkingmachines/inkling:free"
GPT_OSS = "openai/gpt-oss-120b"
SQL = "SELECT COUNT(DISTINCT sku) AS sku_count FROM inventory"
MSGS = [{"role": "user", "content": "q"}]


def _sql_ok(text: str) -> bool:
    return "select" in text.lower() and "from" in text.lower()


def _served(model: str, content: str = SQL) -> tuple[int, dict[str, Any]]:
    return 200, {
        "model": model,
        "choices": [{"message": {"role": "assistant", "content": content}}],
        "served_provider": "groq",
        "served_model": model,
        "served_local": False,
    }


def _pin_unavailable(model: str) -> tuple[int, dict[str, Any]]:
    return 503, {
        "error": {
            "message": "pinned model has no healthy hop",
            "type": "pin_unavailable",
            "model": model,
            "reason": "no_hop",
            "provider": None,
            "retry_after_s": None,
        },
        "served_provider": None,
        "served_model": None,
        "served_local": False,
    }


@pytest.fixture()
def substituting_openvault(armed_openvault, monkeypatch):
    """groq lists gpt-oss, openrouter lists inkling; only groq ever answers.

    The candidate walk is hop order, so inkling is the first candidate: on a tie
    in validity and latency it is the one picked. gpt-oss answers 30 ms slower
    so a tie in validity goes to inkling on latency as well.
    """
    fake = armed_openvault
    fake.hops = [fake.hop("openrouter", 10), fake.hop("groq", 20)]
    fake.catalogue = {"openrouter": [INKLING], "groq": [GPT_OSS]}
    fake.asked = []

    def transport(method: str, path: str, **kwargs: Any) -> tuple[int, Any]:
        if str(path).split("?", 1)[0] != "/v1/chat/completions":
            return fake(method, path, **kwargs)
        fake.calls.append({"method": method, "path": path, "body": dict(kwargs.get("body") or {})})
        body = kwargs.get("body") or {}
        model = str(body.get("model") or "")
        fake.asked.append((model, body.get("strict") is True))
        if model == INKLING:
            return _pin_unavailable(model) if body.get("strict") is True else _served(GPT_OSS)
        time.sleep(0.03)
        return _served(model or GPT_OSS)

    monkeypatch.setattr(openvault_client, "request_json", transport)
    return fake


def _history_non_strict(monkeypatch, calls: int = 4) -> None:
    """Explore both candidates with the flag off, the way the live store filled up."""
    monkeypatch.delenv(fr.STRICT_ENV, raising=False)
    for _ in range(calls):
        out = fr.complete("gen-ask", MSGS, accept=_sql_ok)
        assert out.ok is True
        fr.note_verdict(out.stamp, "gate_pass")


# -- _stats / pick ---------------------------------------------------------------


def test_substitute_validity_credits_the_served_id_not_the_requested(
    substituting_openvault, monkeypatch
) -> None:
    _history_non_strict(monkeypatch)
    assert [m for m, _ in substituting_openvault.asked] == [INKLING, INKLING, GPT_OSS, GPT_OSS]
    stats = fr._stats("gen-ask", [INKLING, GPT_OSS])
    assert stats[INKLING].honored_rate == 0.0
    assert stats[INKLING].served_most == GPT_OSS
    assert stats[INKLING].score is None and stats[INKLING].scored_n == 0
    # All four gpt-oss answers count for gpt-oss, two of them asked for as inkling.
    assert stats[GPT_OSS].scored_n == 4
    assert stats[GPT_OSS].honored_rate == 1.0
    assert stats[INKLING].ineligible.startswith("never honored (0 of 2")


def test_never_honored_id_is_not_picked(substituting_openvault, monkeypatch) -> None:
    _history_non_strict(monkeypatch)
    picked = fr.pick("gen-ask", fr.arming(fresh=True))
    assert picked.requested == GPT_OSS
    assert f"ineligible: {INKLING} (never honored" in picked.reason
    assert "not honored" not in picked.reason
    out = fr.complete("gen-ask", MSGS, accept=_sql_ok)
    assert out.stamp.requested == GPT_OSS and out.stamp.served == GPT_OSS
    assert out.stamp.honored is True


def test_pin_unavailable_counts_against_the_requested_id(
    substituting_openvault, monkeypatch
) -> None:
    monkeypatch.setenv(fr.STRICT_ENV, "1")
    for _ in range(2):
        with pytest.raises(fr.FreeRouteStrictError) as caught:
            fr.complete("gen-ask", MSGS, pin=INKLING)
        assert caught.value.error_type == "pin_unavailable"
    st = fr._stats("gen-ask", [INKLING, GPT_OSS])[INKLING]
    assert st.honored_rate == 0.0
    assert st.ineligible == "never honored (0 of 2; pin refused)"
    picked = fr.pick("gen-ask", fr.arming(fresh=True))
    assert picked.requested == GPT_OSS
    # Still unscored: a refusal carries no validity for any model.
    rows = fr._rows("gen-ask")
    assert [r["error_type"] for r in rows] == ["pin_unavailable", "pin_unavailable"]
    assert all(r["scored"] == 0 for r in rows)


@pytest.mark.parametrize(
    ("status", "error_type"),
    [(503, "openvault_no_keys"), (503, "circuit_open"), (503, "")],
)
def test_other_refusals_keep_current_handling(
    armed_openvault, monkeypatch, status, error_type
) -> None:
    """Keyed on the error type, not the status: other 503s say nothing about the id."""
    armed_openvault.hops = [armed_openvault.hop("groq", 10)]
    armed_openvault.catalogue = {"groq": ["m1", "m2"]}
    for _ in range(2):
        armed_openvault.reply(status=status, error_type=error_type)
        out = fr.complete("t", MSGS, pin="m1")
        assert out.ok is False
    st = fr._stats("t", ["m1", "m2"])["m1"]
    assert st.honored_rate is None
    assert st.ineligible == ""
    assert all(r["scored"] == 0 for r in fr._rows("t"))


def test_partially_honored_id_stays_eligible(armed_openvault) -> None:
    """One honored call is enough to stay in the pick; its score is its own."""
    armed_openvault.hops = [armed_openvault.hop("groq", 10)]
    armed_openvault.catalogue = {"groq": [GPT_OSS, "m2"]}
    armed_openvault.reply(SQL, model=GPT_OSS).reply(SQL, model="m2").reply(SQL, model="m2")
    for _ in range(3):
        out = fr.complete("t", MSGS, pin="m2", accept=_sql_ok)
        fr.note_verdict(out.stamp, "gate_pass")
    st = fr._stats("t", ["m2", GPT_OSS])["m2"]
    assert st.honored_rate == round(2 / 3, 3)
    assert st.ineligible == ""
    assert st.scored_n == 2


# -- Insights (the customer answer) ----------------------------------------------


@pytest.mark.asyncio
async def test_strict_insights_ask_is_served_by_the_requested_model(
    substituting_openvault, monkeypatch
) -> None:
    """No operator bundle, strict on, a non-strict history where inkling was never honored.

    On bcdbb45 this picked inkling (it inherited gpt-oss's validity and won on
    latency), OpenVault refused the strict pin, and Insights answered ABSTAIN.
    """
    monkeypatch.delenv(fr.MODELS_ENV, raising=False)
    bridge = ScriptedBridge(_certified_engine())

    monkeypatch.delenv(fr.STRICT_ENV, raising=False)
    for _ in range(4):
        warm = await insights.run_insights("how many skus", bridge=bridge, ask=True, generate=True)
        assert warm["status"] == "CERTIFIED", warm.get("refuse_reason") or warm.get("answer")
    explored = [m for m, _ in substituting_openvault.asked]
    assert explored.count(INKLING) >= 2 and explored.count(GPT_OSS) >= 2

    monkeypatch.setenv(fr.STRICT_ENV, "1")
    before = len(substituting_openvault.asked)
    env = await insights.run_insights("how many skus", bridge=bridge, ask=True, generate=True)
    strict_calls = substituting_openvault.asked[before:]

    assert env["status"] == "CERTIFIED", env.get("refuse_reason") or env.get("answer")
    assert env["badge"] != "abstain"
    assert "12" in (env.get("answer") or "")
    assert env["values"] == [{"sku_count": 12}]
    stamp = env["generative"]["stamp"]
    assert stamp["requested"] == GPT_OSS
    assert stamp["served"] == stamp["requested"]
    assert stamp["model_requested"] == stamp["model_served"] == GPT_OSS
    assert env["served_model"] == stamp["requested"]
    assert "FreeRouteStrictError" not in str(env["generative"].get("refuse_reason") or "")
    assert strict_calls and all(call == (GPT_OSS, True) for call in strict_calls)
