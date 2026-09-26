import httpx
import pytest
import respx

from CortexOS.decision import (
    ChoiceQuestion,
    KevHttpBackend,
    NoulQuestion,
    RawDecision,
    RulesBackend,
    ScoreQuestion,
    decide,
    tier_question,
)
from CortexOS.execution.model_router import ModelRequest, ModelRouter
from CortexOS.routing.judgment_model import JudgmentModel, JudgmentRequest
from CortexOS.routing.tiers import Tier

KEV = "http://127.0.0.1:8787"
ENDPOINT = f"{KEV}/v1/systemone"


class _Fixed:
    """Backend returning fixed logits, indexed by option name so order does not matter."""

    name = "fixed"

    def __init__(self, by_name: dict[str, float], is_logits: bool = True) -> None:
        self.by_name = by_name
        self.is_logits = is_logits
        self.calls = 0

    def evaluate(self, question, state):
        self.calls += 1
        return RawDecision(
            scores=tuple(self.by_name[n] for n in question.labels),
            is_logits=self.is_logits,
            calibrated=False,
            backend=self.name,
        )


class _Positional:
    """Backend that always prefers the first option: order-sensitive by construction."""

    name = "positional"

    def evaluate(self, question, state):
        k = len(question.labels)
        return RawDecision(
            scores=tuple([0.7] + [0.3 / (k - 1)] * (k - 1)),
            is_logits=False,
            calibrated=True,
            backend=self.name,
        )


# --- model validation -----------------------------------------------------


def test_choice_validation_rejects_duplicates_and_bounds():
    with pytest.raises(ValueError):
        ChoiceQuestion("q", (("a", ""), ("a", "")))
    with pytest.raises(ValueError):
        ChoiceQuestion("q", ())
    with pytest.raises(ValueError):
        ScoreQuestion("q", tuple(f"l{i}" for i in range(256)))
    assert len(ScoreQuestion("q", tuple(f"l{i}" for i in range(255))).labels) == 255


# --- decide() -------------------------------------------------------------


def test_decide_applies_temperature_and_marks_calibrated():
    q = ChoiceQuestion("pick", (("a", ""), ("b", "")))
    backend = _Fixed({"a": 2.0, "b": 0.0})
    hot = decide(q, "s", backend, temperature=1.0, abstain_threshold=0.0)
    cold = decide(q, "s", backend, temperature=4.0, abstain_threshold=0.0)
    assert hot.choice == cold.choice == "a"
    assert hot.probabilities["a"] > cold.probabilities["a"] > 0.5
    assert hot.calibrated is True and hot.temperature == 1.0
    assert hot.abstain is False
    assert decide(q, "s", backend, abstain_threshold=0.0).calibrated is False


def test_decide_abstains_below_threshold():
    q = ChoiceQuestion("pick", (("a", ""), ("b", ""), ("c", "")))
    ans = decide(q, "s", _Fixed({"a": 0.4, "b": 0.35, "c": 0.25}, is_logits=False), abstain_threshold=0.5)
    assert ans.abstain is True
    assert ans.choice == "a"
    assert ans.confidence == pytest.approx((0.4 - 1 / 3) / (1 - 1 / 3))
    assert "threshold" in (ans.abstain_reason or "")


def test_decide_order_sensitivity_abstains():
    q = ChoiceQuestion("pick", (("a", ""), ("b", ""), ("c", "")))
    ans = decide(q, "s", _Positional(), abstain_threshold=0.0, check_order=True)
    assert ans.order_sensitive is True
    assert ans.abstain is True
    stable = decide(q, "s", _Fixed({"a": 1.0, "b": 0.0, "c": -1.0}), abstain_threshold=0.0, check_order=True)
    assert stable.order_sensitive is False
    assert stable.abstain is False
    assert stable.choice == "a"


def test_decide_noul_and_score_payloads():
    noul = decide(NoulQuestion("urgent?"), "s", _Fixed({"true": 3.0, "false": 0.0}), abstain_threshold=0.0)
    assert noul.noul == pytest.approx(noul.probabilities["true"])
    assert noul.noul > 0.9
    score = decide(
        ScoreQuestion("how angry", ("Calm", "Frustrated", "Very angry")),
        "s",
        _Fixed({"0": 0.0, "1": 0.56, "2": 0.44}, is_logits=False),
        abstain_threshold=0.0,
    )
    assert score.score == pytest.approx(1.44)
    assert score.legend == {"0": "Calm", "1": "Frustrated", "2": "Very angry"}


def test_decide_reports_backend_score_length_mismatch_as_degraded():
    class Bad:
        name = "bad"

        def evaluate(self, question, state):
            return RawDecision(scores=(1.0,), is_logits=False, calibrated=False, backend="bad")

    ans = decide(tier_question(), "s", Bad())
    assert ans.degraded is True and ans.abstain is True
    assert "scores" in (ans.cause or "")


# --- RulesBackend ---------------------------------------------------------


def test_rules_backend_is_never_calibrated_and_matches_rules_tier():
    req = JudgmentRequest(request_type="chat", content="please review the stamp duty on this SPA")
    raw = RulesBackend().evaluate(tier_question(), req)
    assert raw.calibrated is False
    ans = decide(tier_question(), req, RulesBackend(), abstain_threshold=0.0)
    assert ans.calibrated is False
    assert ans.choice == Tier.T2.value
    assert ans.probabilities[Tier.T2.value] == pytest.approx(0.92)
    other = RulesBackend().evaluate(NoulQuestion("x"), req)
    assert other.degraded is True


# --- KevHttpBackend -------------------------------------------------------


@pytest.mark.parametrize("url", ["http://example.com:8787", "http://10.0.0.5", "https://kev.internal"])
def test_kev_backend_refuses_non_loopback(url):
    with pytest.raises(ValueError, match="loopback"):
        KevHttpBackend(url)


def test_kev_backend_needs_a_url(monkeypatch):
    monkeypatch.delenv("CORTEX_KEV_URL", raising=False)
    with pytest.raises(ValueError):
        KevHttpBackend()
    assert KevHttpBackend("http://localhost:1/").endpoint == "http://localhost:1/v1/systemone"


@respx.mock
def test_kev_backend_happy_path_choice_noul_score():
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        import json

        body = json.loads(request.content)
        seen.append(body)
        qtype = body["questions"]["q"]["type"]
        if qtype == "choice":
            answer = {"type": "choice", "choice": "T2", "confidence": 0.6,
                      "probabilities": {"T0": 0.05, "T1": 0.1, "T2": 0.7, "T3": 0.15}}
        elif qtype == "noul":
            answer = {"type": "noul", "noul": 0.93}
        else:
            answer = {"type": "score", "score": 1.44, "confidence": 0.78,
                      "legend": {"0": "Calm", "1": "Frustrated", "2": "Very angry"},
                      "probabilities": {"0": 0.0, "1": 0.56, "2": 0.44}}
        return httpx.Response(200, json={"model": "kev-latest", "answers": {"q": answer}})

    respx.post(ENDPOINT).mock(side_effect=handler)
    backend = KevHttpBackend(KEV)

    choice = decide(tier_question(), {"request_type": "chat"}, backend, abstain_threshold=0.5)
    assert choice.choice == "T2"
    assert choice.abstain is False
    assert choice.calibrated is True
    assert choice.confidence == pytest.approx((0.7 - 0.25) / 0.75)
    assert seen[0]["model"] == "kev-latest"
    assert seen[0]["questions"]["q"]["criteria"]["T3"]
    assert seen[0]["state"] == {"request_type": "chat"}

    noul = decide(NoulQuestion("urgent?", true_description="needs a human"), "text", backend)
    assert noul.noul == pytest.approx(0.93)
    assert seen[1]["questions"]["q"]["criteria"] == {"true": "needs a human"}

    score = decide(ScoreQuestion("anger", ("Calm", "Frustrated", "Very angry")), "text", backend)
    assert score.score == pytest.approx(1.44)
    assert seen[2]["questions"]["q"]["criteria"] == ["Calm", "Frustrated", "Very angry"]


@respx.mock
@pytest.mark.parametrize(
    "response, cause_fragment",
    [
        (httpx.Response(500), "http 500"),
        (httpx.Response(200, content=b"not json"), "invalid json"),
        (httpx.Response(200, json={"answers": {"q": {"type": "choice", "probabilities": {"T0": 1.0}}}}), "parse"),
        (httpx.ConnectError("refused"), "transport: ConnectError"),
    ],
)
def test_kev_backend_errors_are_degraded_not_silent(response, cause_fragment):
    route = respx.post(ENDPOINT)
    if isinstance(response, Exception):
        route.mock(side_effect=response)
    else:
        route.mock(return_value=response)
    ans = decide(tier_question(), "s", KevHttpBackend(KEV))
    assert ans.degraded is True
    assert ans.abstain is True
    assert ans.choice is None
    assert cause_fragment in (ans.cause or "")
    assert ans.backend == "kev-http"


# --- JudgmentModel / ModelRouter opt-in ----------------------------------


def test_judgment_model_default_is_unchanged_rules_path():
    jm = JudgmentModel()
    d = jm.decide(JudgmentRequest(request_type="intent_classify", content="x"))
    assert (d.tier, d.confidence, d.reason) == (Tier.T0, 0.99, "deterministic low-tier task")


@respx.mock
def test_router_opt_in_via_env_uses_kev_and_falls_back_loudly(monkeypatch):
    monkeypatch.setenv("CORTEX_KEV_URL", KEV)
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(200, json={"answers": {"q": {
            "type": "choice", "choice": "T3", "confidence": 0.9,
            "probabilities": {"T0": 0.01, "T1": 0.01, "T2": 0.05, "T3": 0.93}}}})

    respx.post(ENDPOINT).mock(side_effect=handler)
    router = ModelRouter()
    res = router.route(ModelRequest(request_type="chat", prompt="hi", default_tier=Tier.T1, max_tier=Tier.T3))
    assert res.tier == Tier.T3
    assert res.reason.startswith("kev-http choice")
    assert calls["n"] == 2  # order check re-asks with reversed options

    # server down: explicit degraded reason, rules answer the tier
    respx.post(ENDPOINT).mock(side_effect=httpx.ConnectError("down"))
    res = router.route(ModelRequest(request_type="chat", prompt="hi", default_tier=Tier.T1, max_tier=Tier.T3))
    assert res.tier == Tier.T1
    assert "kev-http abstained (degraded: transport: ConnectError)" in res.reason
    assert "rules fallback" in res.reason


def test_router_opt_in_refuses_non_loopback_env(monkeypatch):
    monkeypatch.setenv("CORTEX_KEV_URL", "http://kev.example.com")
    with pytest.raises(ValueError, match="loopback"):
        ModelRouter()


def test_judgment_model_constructor_backend_stamps_uncalibrated():
    jm = JudgmentModel(decision_backend=RulesBackend(), abstain_threshold=0.0)
    d = jm.decide(JudgmentRequest(request_type="chat", content="loan tenure question"))
    assert d.tier == Tier.T2
    assert d.reason == "rules-v0 choice (calibrated=False)"
