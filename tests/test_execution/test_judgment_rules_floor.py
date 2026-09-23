"""GH-02 RULES-FLOOR (#242): deterministic tier floors apply after a backend choice.

With a decision backend configured, ``JudgmentModel.decide`` used to return the
backend's tier verbatim. The legal/financial rule and the birthday rule lived only
in ``rules_decide``, so a kev choice of T0 could serve a loan-agreement review.
These tests assert the user-visible artifacts: the DAG node output, the cost
ledger row, the adapter request the spy received, and the routing reason.
"""

from __future__ import annotations

import json
from dataclasses import replace

import pytest
from netie.decision.models import ChoiceQuestion, Question, RawDecision
from netie.execution.dag_runner import ExecutionContext, run_dag
from netie.execution.model_router import ModelRequest, ModelRouter
from netie.fabrication.dsl_parser import parse_dsl
from netie.result import Ok
from netie.routing.adapters.base import AdapterRequest, AdapterResponse, LLMAdapter
from netie.routing.cost_ledger import CostLedger
from netie.routing.judgment_model import JudgmentModel, JudgmentRequest
from netie.routing.tiers import Tier


class FixedChoiceBackend:
    """A decision backend that always answers the same tier with high confidence.

    It answers by label, so the reversed-order check in ``decide()`` agrees with
    the first answer and the backend never abstains.
    """

    name = "stub-fixed"

    def __init__(self, choice: Tier) -> None:
        self.choice = choice
        self.calls = 0

    def evaluate(self, question: Question, state: object) -> RawDecision:
        del state
        self.calls += 1
        assert isinstance(question, ChoiceQuestion)
        scores = tuple(0.97 if label == self.choice.value else 0.01 for label in question.labels)
        return RawDecision(scores=scores, is_logits=False, calibrated=True, backend=self.name)


class SpyAdapter(LLMAdapter):
    def __init__(self) -> None:
        self.requests: list[AdapterRequest] = []

    async def complete(self, req: AdapterRequest) -> AdapterResponse:
        self.requests.append(req)
        return AdapterResponse(content="ok", prompt_tokens=10, completion_tokens=20, latency_ms=1, raw={})

    def cost_myr(self, prompt_tokens: int, completion_tokens: int) -> float:
        return round(prompt_tokens * 0.001 + completion_tokens * 0.002, 6)


def _t0_model() -> JudgmentModel:
    return JudgmentModel(decision_backend=FixedChoiceBackend(Tier.T0), abstain_threshold=0.0)


def _router(jm: JudgmentModel, spy: SpyAdapter) -> ModelRouter:
    return ModelRouter(
        judgment_model=jm,
        adapter_registry={"anthropic": spy, "openai": spy, "self_hosted": spy},
    )


def _parse(dag: dict) -> object:
    r = parse_dsl(json.dumps(dag), "test")
    assert isinstance(r, Ok)
    return r.value


def _one_node_dag(node_id: str, prompt: str, request_type: str | None = None) -> object:
    node: dict = {
        "id": node_id,
        "kind": "llm_judged",
        "default_tier": "T0",
        "max_tier": "T3",
        "prompt": prompt,
        "max_tokens": 50,
        "cost_ceiling_myr": 10.0,
        "inputs": [],
    }
    if request_type is not None:
        node["request_type"] = request_type
    return _parse(
        {
            "version": "2.0",
            "intent_hash": "h",
            "entry_node_id": node_id,
            "output_node_id": "out",
            "nodes": [node, {"id": "out", "kind": "EMIT", "tier": 0, "inputs": [node_id]}],
        }
    )


# --- DAG run: node output + ledger row + adapter request ----------------------


@pytest.mark.asyncio
async def test_dag_loan_review_lands_at_t2_despite_t0_backend():
    spy = SpyAdapter()
    router = _router(_t0_model(), spy)
    ledger = CostLedger()
    dag = _one_node_dag("j1", "review this loan agreement")

    res = await run_dag(dag, ExecutionContext("run_floor_loan"), router, ledger, workflow_cost_ceiling_myr=None)

    assert "j1" in res.outputs
    rows = [r for r in ledger.records() if r.node_id == "j1"]
    assert len(rows) == 1
    assert rows[0].tier == "T2"
    assert rows[0].status == "ok"
    assert rows[0].model == router.tier_models[Tier.T2]
    assert rows[0].cost_myr > 0
    # The adapter actually received the call at the T2 model, not the T0 skip path.
    assert len(spy.requests) == 1
    assert spy.requests[0].model == router.tier_models[Tier.T2]
    node_out = res.outputs["j1"]
    assert node_out.tier == "T2"
    assert node_out.output["content"] == "ok"


@pytest.mark.asyncio
async def test_dag_embedding_request_stays_t0_and_never_hits_adapter():
    spy = SpyAdapter()
    # Backend insists on T3; the deterministic pin must win.
    jm = JudgmentModel(decision_backend=FixedChoiceBackend(Tier.T3), abstain_threshold=0.0)
    router = _router(jm, spy)
    ledger = CostLedger()
    dag = _one_node_dag("emb", "embed this text", request_type="embedding")

    res = await run_dag(dag, ExecutionContext("run_floor_emb"), router, ledger, workflow_cost_ceiling_myr=None)

    rows = [r for r in ledger.records() if r.node_id == "emb"]
    assert len(rows) == 1
    assert rows[0].tier == "T0"
    assert rows[0].cost_myr == 0.0
    assert spy.requests == []
    node_out = res.outputs["emb"]
    assert node_out.tier == "T0"
    raw = node_out.output["raw"]
    assert raw["tier"] == "T0" and raw["skipped_adapter"] is True
    assert "deterministic low-tier pin" in raw["reason"]
    assert "stub-fixed choice T3" in raw["reason"]


# --- Reason strings name the backend choice and the override ---------------------


def test_reason_names_backend_choice_and_legal_floor():
    d = _t0_model().decide(JudgmentRequest(request_type="chat", content="review this loan agreement"))
    assert d.tier == Tier.T2
    assert "stub-fixed choice T0" in d.reason
    assert "legal/financial floor" in d.reason
    assert d.reason.endswith("T2")


@pytest.mark.parametrize("term", JudgmentModel.LEGAL_TERMS)
def test_every_legal_term_floors_a_t1_backend_to_t2(term: str):
    jm = JudgmentModel(decision_backend=FixedChoiceBackend(Tier.T1), abstain_threshold=0.0)
    d = jm.decide(JudgmentRequest(request_type="chat", content=f"please check the {term} for me"))
    assert d.tier == Tier.T2
    assert "legal/financial floor" in d.reason


def test_birthday_request_floors_to_t3_with_override_named():
    d = _t0_model().decide(JudgmentRequest(request_type="chat", content="draft a birthday message"))
    assert d.tier == Tier.T3
    assert "stub-fixed choice T0" in d.reason
    assert "birthday quality floor" in d.reason

    d2 = _t0_model().decide(JudgmentRequest(request_type="birthday_rapport", content="hello"))
    assert d2.tier == Tier.T3
    assert "birthday quality floor" in d2.reason


@pytest.mark.parametrize("req_type", ["embedding", "intent_classify", "sentiment"])
def test_low_tier_request_types_pin_t0_regardless_of_backend(req_type: str):
    jm = JudgmentModel(decision_backend=FixedChoiceBackend(Tier.T3), abstain_threshold=0.0)
    # Even with a legal term in the content, the request type pin decides.
    d = jm.decide(JudgmentRequest(request_type=req_type, content="loan contract nric"))
    assert d.tier == Tier.T0
    assert "deterministic low-tier pin" in d.reason


# --- Floors never lower; at-or-above passes through unchanged ---------------------


@pytest.mark.parametrize("choice", [Tier.T2, Tier.T3])
def test_legal_request_at_or_above_floor_keeps_backend_choice(choice: Tier):
    jm = JudgmentModel(decision_backend=FixedChoiceBackend(choice), abstain_threshold=0.0)
    d = jm.decide(JudgmentRequest(request_type="chat", content="loan tenure question"))
    assert d.tier == choice
    assert d.reason == "stub-fixed choice (calibrated=True)"
    assert "overridden" not in d.reason


def test_birthday_request_at_t3_is_unchanged():
    jm = JudgmentModel(decision_backend=FixedChoiceBackend(Tier.T3), abstain_threshold=0.0)
    d = jm.decide(JudgmentRequest(request_type="chat", content="birthday wishes"))
    assert d.tier == Tier.T3
    assert d.reason == "stub-fixed choice (calibrated=True)"


def test_plain_request_takes_backend_choice_without_floor():
    d = _t0_model().decide(JudgmentRequest(request_type="chat", content="what is the weather"))
    assert d.tier == Tier.T0
    assert d.reason == "stub-fixed choice (calibrated=True)"


@pytest.mark.parametrize("choice", list(Tier))
def test_apply_rules_floor_never_returns_a_lower_tier(choice: Tier):
    jm = JudgmentModel()
    from netie.routing.tiers import TIER_ORDER

    for content in ("loan agreement", "birthday card", "plain text"):
        out = jm.apply_rules_floor(JudgmentRequest(request_type="chat", content=content), choice)
        if out is not None:
            assert TIER_ORDER[out[0]] > TIER_ORDER[choice]


# --- Rules-only path is byte-identical to rules_decide -------------------------------


_FIXED_REQUESTS = (
    JudgmentRequest(request_type="embedding", content="x"),
    JudgmentRequest(request_type="intent_classify", content="loan"),
    JudgmentRequest(request_type="sentiment", content="birthday"),
    JudgmentRequest(request_type="chat", content="draft a birthday message"),
    JudgmentRequest(request_type="birthday_rapport", content="hi"),
    JudgmentRequest(request_type="chat", content="review this loan agreement"),
    JudgmentRequest(request_type="chat", content="what is the nric format"),
    JudgmentRequest(request_type="chat", content="hello"),
    JudgmentRequest(request_type="chat", content="hello", context_size=5000),
    JudgmentRequest(request_type="chat", content="hello", prior_tier_failures=1),
    JudgmentRequest(request_type="chat", content="hello", is_vip=True),
    JudgmentRequest(request_type="chat", content="hello", context_size=5000, is_vip=True),
)


@pytest.mark.parametrize("req", _FIXED_REQUESTS, ids=[f"{r.request_type}:{r.content}" for r in _FIXED_REQUESTS])
def test_rules_only_decide_is_identical_to_rules_decide(req: JudgmentRequest):
    jm = JudgmentModel()
    assert jm.decision_backend is None
    assert jm.decide(req) == jm.rules_decide(replace(req))


def test_router_reason_carries_override_to_routed_call():
    spy = SpyAdapter()
    router = _router(_t0_model(), spy)
    routed = router.route(
        ModelRequest(
            request_type="chat",
            prompt="review this loan agreement",
            default_tier=Tier.T0,
            max_tier=Tier.T3,
        )
    )
    assert routed.tier == Tier.T2
    assert routed.model == router.tier_models[Tier.T2]
    assert "legal/financial floor" in routed.reason
