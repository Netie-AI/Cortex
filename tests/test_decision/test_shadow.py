"""KEV-SHADOW (#248): kev evaluated beside the rules, agreement logged, never serves.

Every test asserts the artifacts an operator sees: the served ``JudgmentDecision``
(or the ``ModelRouter`` result built from it) and the JSONL shadow file read
back. The served decision must be byte-identical to ``rules_decide`` whatever
kev answers, times out or raises; the shadow file must never carry prompt text.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
import respx
from netie.decision import shadow
from netie.decision.models import ChoiceQuestion, RawDecision
from netie.decision.shadow import ShadowEvaluator, read_rows, summary
from netie.execution.model_router import ModelRequest, ModelRouter
from netie.routing.judgment_model import JudgmentDecision, JudgmentModel, JudgmentRequest
from netie.routing.tiers import Tier

KEV = "http://127.0.0.1:8787"
ENDPOINT = f"{KEV}/v1/systemone"
SECRET = "PLANTED-SHADOW-SECRET-4c1d9e-never-logged"
TIERS = [t.value for t in Tier]

# A fixed request set spanning every rules branch: T0 pin, birthday floor, legal
# floor, short/long context, prior failures, VIP.
REQUESTS: list[JudgmentRequest] = [
    JudgmentRequest(request_type="intent_classify", content="route me"),
    JudgmentRequest(request_type="chat", content="wish him a happy birthday"),
    JudgmentRequest(request_type="chat", content=f"review the stamp duty on this SPA {SECRET}"),
    JudgmentRequest(request_type="chat", content="hello there"),
    JudgmentRequest(request_type="chat", content="x" * 10, context_size=9000),
    JudgmentRequest(request_type="chat", content="retry please", prior_tier_failures=1),
    JudgmentRequest(request_type="chat", content="vip hello", is_vip=True),
]


@pytest.fixture
def shadow_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "tier_shadow.jsonl"
    monkeypatch.setenv(shadow.PATH_ENV, str(path))
    monkeypatch.setenv("CORTEX_DECISION_LOG_PATH", str(tmp_path / "tier_decisions.jsonl"))
    shadow.reset_write_failures()
    return path


def _rules_state(body: dict) -> Tier:
    """Recover the rules tier from the state kev was sent, so the mock can disagree."""
    s = body["state"]
    req = JudgmentRequest(
        request_type=s["request_type"],
        content=s["content"],
        context_size=s["context_size"],
        prior_tier_failures=s["prior_tier_failures"],
        user_tier_budget=Tier(s["user_tier_budget"]),
        is_vip=s["is_vip"],
    )
    return JudgmentModel().rules_decide(req).tier


def _answer(choice: str) -> dict:
    probs = {t: (0.91 if t == choice else 0.03) for t in TIERS}
    return {"answers": {"q": {"type": "choice", "choice": choice, "confidence": 0.9, "probabilities": probs}}}


def _disagreeing_handler(calls: list[dict]):
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        calls.append(body)
        rules_tier = _rules_state(body)
        other = next(t for t in TIERS if t != rules_tier.value)
        return httpx.Response(200, json=_answer(other))

    return handler


def _same(a: JudgmentDecision, b: JudgmentDecision) -> bool:
    return (a.tier, a.confidence, a.reason) == (b.tier, b.confidence, b.reason)


# --- acceptance: rules serve, kev observed, disagreement logged ---------------


@respx.mock
def test_shadow_serves_rules_and_logs_disagreement_per_decision(monkeypatch, shadow_file):
    monkeypatch.setenv("CORTEX_KEV_URL", KEV)
    monkeypatch.setenv("CORTEX_KEV_SHADOW", "1")
    calls: list[dict] = []
    respx.post(ENDPOINT).mock(side_effect=_disagreeing_handler(calls))

    jm = JudgmentModel.from_env()
    assert jm.decision_backend is None, "shadow mode must not install a serving backend"
    assert jm.shadow is not None

    rules = JudgmentModel()
    for req in REQUESTS:
        served = jm.decide(req)
        assert _same(served, rules.rules_decide(req)), req
        assert "kev" not in served.reason

    rows = read_rows(shadow_file)
    assert len(rows) == len(REQUESTS)
    assert len(calls) == 2 * len(REQUESTS), "decide(check_order=True) asks twice per decision"
    for row, req in zip(rows, REQUESTS, strict=True):
        assert row["schema_version"] == 1
        assert row["backend"] == "kev-http"
        assert row["rules_tier"] == rules.rules_decide(req).tier.value
        assert row["kev_choice"] in TIERS and row["kev_choice"] != row["rules_tier"]
        assert set(row["kev_probs"]) == set(TIERS)
        assert row["kev_confidence"] == pytest.approx((0.91 - 0.25) / 0.75)
        assert row["abstain"] is False
        assert row["degraded"] is False
        assert row["order_sensitive"] is False
        assert row["agree"] is False
        assert len(row["state_hash"]) == 64
    assert len({r["state_hash"] for r in rows}) == len(REQUESTS)

    text = shadow_file.read_text(encoding="utf-8")
    assert SECRET not in text
    assert "stamp duty" not in text
    assert "content" not in json.loads(text.splitlines()[0])

    s = summary(shadow_file)
    assert s["n"] == len(REQUESTS)
    assert s["agree"] == 0
    assert s["degraded"] == 0
    assert s["agreement_rate"] == 0.0
    assert s["sufficient"] is False


@respx.mock
def test_shadow_through_model_router_serves_rules_tier(monkeypatch, shadow_file):
    """The router result, the artifact executors consume, is the rules tier under shadow."""
    monkeypatch.setenv("CORTEX_KEV_URL", KEV)
    monkeypatch.setenv("CORTEX_KEV_SHADOW", "1")
    respx.post(ENDPOINT).mock(side_effect=_disagreeing_handler([]))

    router = ModelRouter()
    res = router.route(ModelRequest(request_type="chat", prompt="hi", default_tier=Tier.T1, max_tier=Tier.T3))
    assert res.tier == Tier.T1
    assert res.reason == "heuristic routing fallback"
    legal = router.route(
        ModelRequest(request_type="chat", prompt="loan agreement review", default_tier=Tier.T1, max_tier=Tier.T3)
    )
    assert legal.tier == Tier.T2
    assert legal.reason == "financial/legal terms detected"

    rows = read_rows(shadow_file)
    assert [r["rules_tier"] for r in rows] == ["T1", "T2"]
    assert all(r["agree"] is False for r in rows)


@respx.mock
def test_shadow_agreement_row_when_kev_matches(monkeypatch, shadow_file):
    monkeypatch.setenv("CORTEX_KEV_URL", KEV)
    monkeypatch.setenv("CORTEX_KEV_SHADOW", "1")

    def agreeing(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_answer(_rules_state(json.loads(request.content)).value))

    respx.post(ENDPOINT).mock(side_effect=agreeing)
    jm = JudgmentModel.from_env()
    req = JudgmentRequest(request_type="chat", content="hello")
    assert _same(jm.decide(req), JudgmentModel().rules_decide(req))
    rows = read_rows(shadow_file)
    assert len(rows) == 1 and rows[0]["agree"] is True and rows[0]["kev_choice"] == "T1"
    assert summary(shadow_file)["agreement_rate"] == 1.0


# --- acceptance: kev down, slow, degraded or raising ---------------------------


@respx.mock
@pytest.mark.parametrize(
    "side_effect, cause_fragment",
    [
        (httpx.ReadTimeout("slow"), "transport: ReadTimeout"),
        (httpx.ConnectError("down"), "transport: ConnectError"),
        (httpx.Response(500), "http 500"),
        (httpx.Response(200, json={"answers": {"q": {"type": "choice", "probabilities": {"T0": 1.0}}}}), "parse"),
    ],
)
def test_shadow_kev_failure_serves_identical_rules_and_records_degraded(
    monkeypatch, shadow_file, side_effect, cause_fragment
):
    monkeypatch.setenv("CORTEX_KEV_URL", KEV)
    monkeypatch.setenv("CORTEX_KEV_SHADOW", "1")
    route = respx.post(ENDPOINT)
    if isinstance(side_effect, Exception):
        route.mock(side_effect=side_effect)
    else:
        route.mock(return_value=side_effect)

    jm = JudgmentModel.from_env()
    rules = JudgmentModel()
    for req in REQUESTS:
        served = jm.decide(req)
        assert _same(served, rules.rules_decide(req))

    rows = read_rows(shadow_file)
    assert len(rows) == len(REQUESTS)
    for row in rows:
        assert row["degraded"] is True
        assert row["abstain"] is True
        assert row["agree"] is False
        assert row["kev_choice"] is None
        assert row["kev_probs"] is None
        assert row["kev_confidence"] is None
        assert cause_fragment in row["cause"]
    s = summary(shadow_file)
    assert s["degraded"] == len(REQUESTS)
    assert s["agreement_rate"] == 0.0
    assert shadow.write_failures() == 0


def test_shadow_backend_that_raises_is_a_degraded_row(shadow_file):
    class Boom:
        name = "boom"

        def evaluate(self, question, state):
            raise RuntimeError("kev exploded")

    jm = JudgmentModel(shadow=ShadowEvaluator(Boom()))
    req = JudgmentRequest(request_type="chat", content="hello")
    assert _same(jm.decide(req), JudgmentModel().rules_decide(req))
    rows = read_rows(shadow_file)
    assert len(rows) == 1
    assert rows[0]["degraded"] is True
    assert rows[0]["cause"] == "raised RuntimeError"
    assert rows[0]["backend"] == "boom"
    assert "kev exploded" not in shadow_file.read_text(encoding="utf-8")


def test_shadow_order_sensitive_backend_is_recorded(shadow_file):
    class Positional:
        name = "positional"

        def evaluate(self, question, state):
            assert isinstance(question, ChoiceQuestion)
            k = len(question.labels)
            return RawDecision(
                scores=tuple([0.7] + [0.3 / (k - 1)] * (k - 1)),
                is_logits=False,
                calibrated=True,
                backend=self.name,
            )

    jm = JudgmentModel(shadow=ShadowEvaluator(Positional(), abstain_threshold=0.0))
    req = JudgmentRequest(request_type="chat", content="hello")
    assert _same(jm.decide(req), JudgmentModel().rules_decide(req))
    row = read_rows(shadow_file)[0]
    assert row["order_sensitive"] is True
    assert row["abstain"] is True
    assert row["degraded"] is False
    assert row["agree"] is False
    assert summary(shadow_file)["order_sensitive"] == 1


def test_shadow_unwritable_path_never_raises_and_is_counted(tmp_path, monkeypatch):
    # The parent is a regular file, so mkdir/open fail for any user, root included.
    blocker = tmp_path / "blocker"
    blocker.write_text("not a directory", encoding="utf-8")
    monkeypatch.setenv(shadow.PATH_ENV, str(blocker / "tier_shadow.jsonl"))
    shadow.reset_write_failures()
    jm = JudgmentModel(shadow=ShadowEvaluator(_Fixed("T3")))
    req = JudgmentRequest(request_type="chat", content="hello")
    assert _same(jm.decide(req), JudgmentModel().rules_decide(req))
    assert shadow.write_failures() == 1
    assert "append" in (shadow.last_write_error() or "")
    assert blocker.read_text(encoding="utf-8") == "not a directory"


# --- acceptance: shadow unset behaves exactly as today --------------------------


@respx.mock
def test_shadow_unset_with_url_installs_serving_backend(monkeypatch, shadow_file):
    monkeypatch.delenv("CORTEX_KEV_SHADOW", raising=False)
    monkeypatch.setenv("CORTEX_KEV_URL", KEV)
    respx.post(ENDPOINT).mock(return_value=httpx.Response(200, json=_answer("T3")))
    jm = JudgmentModel.from_env()
    assert jm.shadow is None
    assert jm.decision_backend is not None
    d = jm.decide(JudgmentRequest(request_type="chat", content="hello"))
    assert d.tier == Tier.T3
    assert d.reason.startswith("kev-http choice")
    assert not shadow_file.exists()


@pytest.mark.parametrize("value", ["0", "false", "", "no"])
def test_shadow_falsy_values_do_not_enable(monkeypatch, shadow_file, value):
    monkeypatch.setenv("CORTEX_KEV_SHADOW", value)
    monkeypatch.delenv("CORTEX_KEV_URL", raising=False)
    jm = JudgmentModel.from_env()
    assert jm.shadow is None and jm.decision_backend is None
    d = jm.decide(JudgmentRequest(request_type="intent_classify", content="x"))
    assert (d.tier, d.confidence, d.reason) == (Tier.T0, 0.99, "deterministic low-tier task")
    assert not shadow_file.exists()


def test_shadow_without_url_is_plain_rules(monkeypatch, shadow_file):
    monkeypatch.setenv("CORTEX_KEV_SHADOW", "1")
    monkeypatch.delenv("CORTEX_KEV_URL", raising=False)
    jm = JudgmentModel.from_env()
    assert jm.shadow is None and jm.decision_backend is None
    jm.decide(JudgmentRequest(request_type="chat", content="hello"))
    assert not shadow_file.exists()


def test_shadow_refuses_non_loopback_url(monkeypatch):
    monkeypatch.setenv("CORTEX_KEV_SHADOW", "1")
    monkeypatch.setenv("CORTEX_KEV_URL", "http://kev.example.com")
    with pytest.raises(ValueError, match="loopback"):
        JudgmentModel.from_env()


def test_shadow_is_not_consulted_when_a_serving_backend_exists(shadow_file):
    """A serving backend and a shadow together: the shadow stays silent, not doubled."""
    observed = _Fixed("T1")
    jm = JudgmentModel(decision_backend=_Fixed("T3"), abstain_threshold=0.0, shadow=ShadowEvaluator(observed))
    d = jm.decide(JudgmentRequest(request_type="chat", content="hello"))
    assert d.tier == Tier.T3
    assert observed.calls == 0
    assert not shadow_file.exists()


# --- summary and row hygiene ---------------------------------------------------


def test_summary_on_missing_file_is_empty_and_insufficient(tmp_path):
    s = summary(tmp_path / "absent.jsonl")
    assert s == {
        "n": 0,
        "agree": 0,
        "degraded": 0,
        "abstained": 0,
        "order_sensitive": 0,
        "agreement_rate": None,
        "min_n": 300,
        "sufficient": False,
    }


def test_summary_counts_and_skips_malformed_lines(tmp_path):
    path = tmp_path / "s.jsonl"
    lines = [
        json.dumps({"agree": True, "degraded": False, "abstain": False}),
        json.dumps({"agree": False, "degraded": True, "abstain": True}),
        json.dumps({"agree": False, "degraded": False, "abstain": True}),
        "{not json",
        json.dumps({"agree": True, "degraded": False, "abstain": False}),
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    s = summary(path)
    assert (s["n"], s["agree"], s["degraded"], s["abstained"]) == (4, 2, 1, 1)
    assert s["agreement_rate"] == pytest.approx(0.5)
    assert s["sufficient"] is False
    assert summary(path, min_n=4)["sufficient"] is True


def test_append_row_refuses_prompt_bearing_keys(tmp_path):
    path = tmp_path / "s.jsonl"
    shadow.reset_write_failures()
    assert shadow.append_row({"agree": True, "content": SECRET}, path) is False
    assert shadow.append_row({"agree": True, "state": {"content": SECRET}}, path) is False
    assert not path.exists()
    assert shadow.write_failures() == 2


def test_default_shadow_path_is_gitignored_engine_dir(monkeypatch):
    monkeypatch.delenv(shadow.PATH_ENV, raising=False)
    p = shadow.shadow_path()
    assert p.name == "tier_shadow.jsonl"
    assert p.parent.name == "engine" and p.parent.parent.name == "data"


class _Fixed:
    """Backend that answers by label, so the reversed-order check agrees."""

    name = "fixed"

    def __init__(self, choice: str) -> None:
        self.choice = choice
        self.calls = 0

    def evaluate(self, question, state):
        self.calls += 1
        scores = tuple(0.91 if label == self.choice else 0.03 for label in question.labels)
        return RawDecision(scores=scores, is_logits=False, calibrated=True, backend=self.name)
