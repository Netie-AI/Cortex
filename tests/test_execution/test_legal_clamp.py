"""H2-LEGAL-CLAMP (#251): word-boundary legal terms; a safety floor above the DSL
``max_tier`` fails closed.

Two verified problems on the base:

1. ``ModelRouter.route`` clamped every judged tier with ``bounded_tier``, so the
   GH-02 legal/financial T2 floor was served at T1 whenever the DSL node declared
   ``max_tier=T1``.
2. ``LEGAL_TERMS`` matched by substring: ``spa`` in ``space``, ``legal`` in
   ``illegal``, ``loan`` in ``loaner``. Failing closed on that would have refused
   ordinary prompts, so the match is now whole-word and case-insensitive.

The tests assert the user-visible artifacts: the run result (typed error or
served tier), the cost ledger rows, and the adapter request the spy received.
"""

from __future__ import annotations

import json

import pytest
from netie.decision.models import ChoiceQuestion, Question, RawDecision
from netie.execution.dag_runner import ExecutionContext, estimate_node_cost, run_dag
from netie.execution.model_router import (
    FloorRefusalAdapter,
    ModelRequest,
    ModelRouter,
    TierFloorAboveCap,
)
from netie.fabrication.dsl_parser import parse_dsl
from netie.result import Ok
from netie.routing.adapters.base import AdapterRequest, AdapterResponse, LLMAdapter
from netie.routing.cost_ledger import CostLedger
from netie.routing.judgment_model import JudgmentModel, JudgmentRequest
from netie.routing.tiers import TIER_ORDER, Tier, bounded_tier


class SpyAdapter(LLMAdapter):
    def __init__(self) -> None:
        self.requests: list[AdapterRequest] = []

    async def complete(self, req: AdapterRequest) -> AdapterResponse:
        self.requests.append(req)
        return AdapterResponse(content="ok", prompt_tokens=10, completion_tokens=20, latency_ms=1, raw={})

    def cost_myr(self, prompt_tokens: int, completion_tokens: int) -> float:
        return round(prompt_tokens * 0.001 + completion_tokens * 0.002, 6)


class FixedChoiceBackend:
    """Decision backend that always answers the same tier and never abstains."""

    name = "stub-fixed"

    def __init__(self, choice: Tier) -> None:
        self.choice = choice

    def evaluate(self, question: Question, state: object) -> RawDecision:
        del state
        assert isinstance(question, ChoiceQuestion)
        scores = tuple(0.97 if label == self.choice.value else 0.01 for label in question.labels)
        return RawDecision(scores=scores, is_logits=False, calibrated=True, backend=self.name)


def _router(spy: SpyAdapter, jm: JudgmentModel | None = None) -> ModelRouter:
    return ModelRouter(
        judgment_model=jm or JudgmentModel(),
        adapter_registry={"anthropic": spy, "openai": spy, "self_hosted": spy},
    )


def _parse(dag: dict) -> object:
    r = parse_dsl(json.dumps(dag), "test")
    assert isinstance(r, Ok)
    return r.value


def _one_node_dag(node_id: str, prompt: str, *, default_tier: str = "T1", max_tier: str = "T1") -> object:
    node: dict = {
        "id": node_id,
        "kind": "llm_judged",
        "default_tier": default_tier,
        "max_tier": max_tier,
        "prompt": prompt,
        "max_tokens": 50,
        "cost_ceiling_myr": 10.0,
        "inputs": [],
    }
    return _parse(
        {
            "version": "2.0",
            "intent_hash": "h",
            "entry_node_id": node_id,
            "output_node_id": "out",
            "nodes": [node, {"id": "out", "kind": "EMIT", "tier": 0, "inputs": [node_id]}],
        }
    )


# --- DAG: legal floor above the cap refuses; nothing is served ---------------------


@pytest.mark.asyncio
async def test_dag_loan_review_in_t1_node_is_refused_not_served_at_t1():
    spy = SpyAdapter()
    router = _router(spy)
    ledger = CostLedger()
    dag = _one_node_dag("j1", "review this loan agreement", max_tier="T1")

    with pytest.raises(TierFloorAboveCap) as ei:
        await run_dag(dag, ExecutionContext("run_clamp_loan"), router, ledger, workflow_cost_ceiling_myr=None)

    exc = ei.value
    assert exc.floor == "legal/financial floor"
    assert exc.judged_tier == Tier.T2
    assert exc.max_tier == Tier.T1
    msg = str(exc)
    assert "legal/financial floor" in msg and "T2" in msg and "T1" in msg
    assert "refusing to serve below the floor" in msg

    # No adapter call was made.
    assert spy.requests == []

    # The existing error path wrote the ledger row.
    rows = [r for r in ledger.records() if r.node_id == "j1"]
    assert len(rows) == 1
    assert rows[0].status == "error"
    assert rows[0].cost_myr == 0.0
    assert rows[0].tier == "T2"
    assert rows[0].error is not None and "legal/financial floor" in rows[0].error
    assert "T1" in rows[0].error


@pytest.mark.asyncio
async def test_dag_vip_floor_above_cap_is_refused():
    spy = SpyAdapter()
    router = _router(spy)
    ledger = CostLedger()
    dag = _one_node_dag("v1", "hello there", max_tier="T1")

    with pytest.raises(TierFloorAboveCap) as ei:
        await run_dag(
            dag,
            ExecutionContext("run_clamp_vip", {"is_vip": True}),
            router,
            ledger,
            workflow_cost_ceiling_myr=None,
        )

    assert ei.value.floor == "vip floor"
    assert ei.value.judged_tier == Tier.T2
    assert ei.value.max_tier == Tier.T1
    assert spy.requests == []
    rows = [r for r in ledger.records() if r.node_id == "v1"]
    assert len(rows) == 1 and rows[0].status == "error"
    assert rows[0].error is not None and "vip floor" in rows[0].error


@pytest.mark.asyncio
async def test_dag_legal_floor_with_t0_backend_in_t1_node_is_refused():
    """The kev backend path carries the floor too: a T0 choice floored to T2 refuses at a T1 cap."""
    spy = SpyAdapter()
    jm = JudgmentModel(decision_backend=FixedChoiceBackend(Tier.T0), abstain_threshold=0.0)
    router = _router(spy, jm)
    ledger = CostLedger()
    dag = _one_node_dag("k1", "review this loan agreement", max_tier="T1")

    with pytest.raises(TierFloorAboveCap) as ei:
        await run_dag(dag, ExecutionContext("run_clamp_kev"), router, ledger, workflow_cost_ceiling_myr=None)

    assert ei.value.floor == "legal/financial floor"
    assert spy.requests == []
    rows = [r for r in ledger.records() if r.node_id == "k1"]
    assert len(rows) == 1 and rows[0].status == "error"


class UniformScoreBackend:
    """Decision backend whose scores are flat, so ``decide`` abstains (order_sensitive)."""

    name = "stub-uniform"

    def evaluate(self, question: Question, state: object) -> RawDecision:
        del state
        assert isinstance(question, ChoiceQuestion)
        n = len(question.labels)
        return RawDecision(scores=tuple([1.0 / n] * n), is_logits=False, calibrated=True, backend=self.name)


async def _assert_dag_refused_below_legal_floor(node_id: str, prompt: str, jm: JudgmentModel | None) -> None:
    spy = SpyAdapter()
    router = _router(spy, jm)
    ledger = CostLedger()
    dag = _one_node_dag(node_id, prompt, max_tier="T1")

    with pytest.raises(TierFloorAboveCap) as ei:
        await run_dag(dag, ExecutionContext(f"run_clamp_{node_id}"), router, ledger, workflow_cost_ceiling_myr=None)

    exc = ei.value
    assert exc.floor == "legal/financial floor"
    assert exc.judged_tier == Tier.T2
    assert exc.max_tier == Tier.T1
    assert "refusing to serve below the floor" in str(exc)
    # No adapter call was made: the T1 model never saw the loan review.
    assert spy.requests == []
    rows = [r for r in ledger.records() if r.node_id == node_id]
    assert len(rows) == 1
    assert rows[0].status == "error"
    assert rows[0].tier == "T2"
    assert rows[0].cost_myr == 0.0
    assert rows[0].error is not None and "legal/financial floor" in rows[0].error


@pytest.mark.asyncio
async def test_dag_legal_floor_with_t2_backend_in_t1_node_is_refused():
    """Verifier finding on #251: a backend already at the floor still carries it.

    ``apply_rules_floor`` returns ``None`` when the choice is at or above T2, and
    the decision used to leave ``floor=None``, so the router clamped the loan
    review to T1 and served it with a green ``ok`` row.
    """
    await _assert_dag_refused_below_legal_floor(
        "kev2", "review this loan agreement", JudgmentModel(decision_backend=FixedChoiceBackend(Tier.T2))
    )


@pytest.mark.asyncio
async def test_dag_legal_floor_with_t3_backend_in_t1_node_is_refused():
    await _assert_dag_refused_below_legal_floor(
        "kev3", "review this loan agreement", JudgmentModel(decision_backend=FixedChoiceBackend(Tier.T3))
    )


@pytest.mark.asyncio
async def test_dag_legal_floor_survives_backend_abstain_in_t1_node():
    """Verifier finding on #251: the rules fallback after an abstain kept its tier but dropped its floor."""
    jm = JudgmentModel(decision_backend=UniformScoreBackend(), abstain_threshold=0.9)
    d = jm.decide(JudgmentRequest(request_type="chat", content="review this loan agreement"))
    assert "abstained" in d.reason and "rules fallback" in d.reason
    await _assert_dag_refused_below_legal_floor("kevabs", "review this loan agreement", jm)


@pytest.mark.asyncio
async def test_dag_birthday_plus_legal_in_t1_node_is_refused():
    """Verifier finding on #251: the birthday branch shadowed the legal floor.

    ``rules_decide`` returned the birthday quality floor (``floor=None``) for any
    prompt that also had legal terms, so the loan review was clamped to T1.
    """
    await _assert_dag_refused_below_legal_floor("bday", "birthday card, then review this loan agreement", None)


@pytest.mark.asyncio
async def test_dag_birthday_plus_legal_with_t3_backend_in_t1_node_is_refused():
    await _assert_dag_refused_below_legal_floor(
        "bdaykev",
        "birthday card, then review this loan agreement",
        JudgmentModel(decision_backend=FixedChoiceBackend(Tier.T3)),
    )


@pytest.mark.parametrize(
    "jm",
    [
        JudgmentModel(),
        JudgmentModel(decision_backend=FixedChoiceBackend(Tier.T0), abstain_threshold=0.0),
        JudgmentModel(decision_backend=FixedChoiceBackend(Tier.T3), abstain_threshold=0.0),
        JudgmentModel(decision_backend=UniformScoreBackend(), abstain_threshold=0.9),
    ],
    ids=["rules", "backend_t0", "backend_t3", "backend_abstain"],
)
def test_birthday_plus_legal_decision_carries_legal_floor(jm: JudgmentModel):
    d = jm.decide(JudgmentRequest(request_type="chat", content="birthday card, then review this loan agreement"))
    assert d.floor == "legal/financial floor"
    assert d.floor_tier == Tier.T2
    assert TIER_ORDER[d.tier] >= TIER_ORDER[Tier.T2]


# --- Floor at or under the cap: served at or above the floor, never refused ---------


@pytest.mark.asyncio
async def test_dag_t3_backend_on_legal_prompt_in_t2_node_is_clamped_to_t2_not_refused():
    """The cap satisfies the floor, so the backend overflow keeps the clamp (no false refusal)."""
    spy = SpyAdapter()
    router = _router(spy, JudgmentModel(decision_backend=FixedChoiceBackend(Tier.T3), abstain_threshold=0.0))
    ledger = CostLedger()
    dag = _one_node_dag("k2", "review this loan agreement", max_tier="T2")

    res = await run_dag(dag, ExecutionContext("run_clamp_kev_t2cap"), router, ledger, workflow_cost_ceiling_myr=None)

    assert res.outputs["k2"].tier == "T2"
    rows = [r for r in ledger.records() if r.node_id == "k2"]
    assert len(rows) == 1 and rows[0].status == "ok" and rows[0].tier == "T2"
    assert len(spy.requests) == 1
    assert spy.requests[0].model == router.tier_models[Tier.T2]


@pytest.mark.asyncio
async def test_dag_birthday_only_prompt_in_t1_node_keeps_the_clamp():
    """Birthday without legal terms is a quality floor: still served at the cap."""
    spy = SpyAdapter()
    router = _router(spy)
    ledger = CostLedger()
    dag = _one_node_dag("b1", "write a birthday card for my aunt", max_tier="T1")

    res = await run_dag(dag, ExecutionContext("run_clamp_bday_only"), router, ledger, workflow_cost_ceiling_myr=None)

    assert res.outputs["b1"].tier == "T1"
    rows = [r for r in ledger.records() if r.node_id == "b1"]
    assert len(rows) == 1 and rows[0].status == "ok" and rows[0].tier == "T1"
    assert len(spy.requests) == 1
    assert spy.requests[0].model == router.tier_models[Tier.T1]


@pytest.mark.parametrize("choice", [Tier.T2, Tier.T3])
def test_backend_at_or_above_floor_on_legal_prompt_keeps_choice_and_floor(choice: Tier):
    jm = JudgmentModel(decision_backend=FixedChoiceBackend(choice), abstain_threshold=0.0)
    d = jm.decide(JudgmentRequest(request_type="chat", content="review this loan agreement"))
    assert d.tier == choice
    assert d.reason == "stub-fixed choice (calibrated=True)"
    assert d.floor == "legal/financial floor"
    assert d.floor_tier == Tier.T2


# --- DAG: no false positive; ordinary prompts are served ----------------------------


@pytest.mark.asyncio
async def test_dag_conference_space_in_t1_node_is_served_at_t1():
    spy = SpyAdapter()
    router = _router(spy)
    ledger = CostLedger()
    dag = _one_node_dag("s1", "book the conference space", max_tier="T1")

    res = await run_dag(dag, ExecutionContext("run_clamp_space"), router, ledger, workflow_cost_ceiling_myr=None)

    assert res.outputs["s1"].tier == "T1"
    assert res.outputs["s1"].output["content"] == "ok"
    rows = [r for r in ledger.records() if r.node_id == "s1"]
    assert len(rows) == 1
    assert rows[0].status == "ok" and rows[0].tier == "T1"
    assert rows[0].model == router.tier_models[Tier.T1]
    assert len(spy.requests) == 1
    assert spy.requests[0].model == router.tier_models[Tier.T1]


@pytest.mark.asyncio
async def test_dag_conference_space_in_uncapped_node_lands_at_t1_not_t2():
    """With no cap the substring bug used to serve ``space`` at T2. Now it is T1."""
    spy = SpyAdapter()
    router = _router(spy)
    ledger = CostLedger()
    dag = _one_node_dag("s2", "book the conference space", max_tier="T3")

    res = await run_dag(dag, ExecutionContext("run_clamp_space_t3"), router, ledger, workflow_cost_ceiling_myr=None)

    assert res.outputs["s2"].tier == "T1"
    rows = [r for r in ledger.records() if r.node_id == "s2"]
    assert rows[0].tier == "T1" and rows[0].status == "ok"
    assert spy.requests[0].model == router.tier_models[Tier.T1]


# --- Non-floor overflow keeps today's clamp ------------------------------------------


@pytest.mark.asyncio
async def test_dag_large_context_heuristic_t2_is_clamped_to_t1_cap():
    spy = SpyAdapter()
    router = _router(spy)
    ledger = CostLedger()
    long_prompt = "summarise the meeting notes " * 120  # > 2500 chars, no legal term
    assert len(long_prompt) > 2500
    dag = _one_node_dag("h1", long_prompt, max_tier="T1")

    res = await run_dag(dag, ExecutionContext("run_clamp_heur"), router, ledger, workflow_cost_ceiling_myr=None)

    assert res.outputs["h1"].tier == "T1"
    rows = [r for r in ledger.records() if r.node_id == "h1"]
    assert rows[0].status == "ok" and rows[0].tier == "T1"
    assert len(spy.requests) == 1
    assert spy.requests[0].model == router.tier_models[Tier.T1]


def test_backend_t3_choice_for_plain_prompt_is_clamped_not_refused():
    spy = SpyAdapter()
    jm = JudgmentModel(decision_backend=FixedChoiceBackend(Tier.T3), abstain_threshold=0.0)
    router = _router(spy, jm)
    routed = router.route(
        ModelRequest(request_type="chat", prompt="what is the weather", default_tier=Tier.T1, max_tier=Tier.T1)
    )
    assert routed.tier == Tier.T1
    assert routed.adapter is spy


def test_birthday_quality_floor_keeps_the_clamp():
    """Birthday is a quality floor, not a safety floor: existing DSL behaviour is kept."""
    spy = SpyAdapter()
    routed = _router(spy).route(
        ModelRequest(
            request_type="birthday_rapport", prompt="send a birthday message", default_tier=Tier.T1, max_tier=Tier.T2
        )
    )
    assert routed.tier == Tier.T2
    assert routed.adapter is spy


# --- No floor: route exactly as today ----------------------------------------------


_PLAIN_PROMPTS = ("hello", "what is the weather", "summarise this paragraph", "x" * 3000)


_VALID_BOUNDS = [(d, m) for d in Tier for m in Tier if TIER_ORDER[d] <= TIER_ORDER[m]]


@pytest.mark.parametrize("prompt", _PLAIN_PROMPTS)
@pytest.mark.parametrize(("default_tier", "max_tier"), _VALID_BOUNDS)
def test_no_floor_routes_exactly_as_bounded_tier(prompt: str, default_tier: Tier, max_tier: Tier):
    spy = SpyAdapter()
    router = _router(spy)
    req = ModelRequest(request_type="chat", prompt=prompt, default_tier=default_tier, max_tier=max_tier)
    expected = bounded_tier(
        JudgmentModel().rules_decide(JudgmentRequest("chat", prompt, context_size=len(prompt))).tier,
        default_tier,
        max_tier,
    )
    routed = router.route(req)
    assert routed.tier == expected
    assert routed.model == router.tier_models[expected]
    assert routed.adapter is spy
    assert not isinstance(routed.adapter, FloorRefusalAdapter)


def test_legal_floor_at_or_under_cap_is_served_at_floor():
    spy = SpyAdapter()
    routed = _router(spy).route(
        ModelRequest(request_type="chat", prompt="review this loan agreement", default_tier=Tier.T1, max_tier=Tier.T2)
    )
    assert routed.tier == Tier.T2
    assert routed.adapter is spy


# --- Word-boundary matching: no-false-positive corpus -----------------------------------

NON_LEGAL_CORPUS: tuple[str, ...] = (
    "book the conference space for monday",
    "the parking space is full",
    "how much disk space is left",
    "that was done illegally",
    "an illegal move in chess",
    "the paralegal team lunch is at noon",
    "please return the loaner car by friday",
    "the contractor arrives at nine",
    "our subcontractor changed the schedule",
    "the contraction of the market slowed hiring",
    "she signed up for the agreementless trial",
    "disagreement about the colour scheme",
    "tenured professor gives a talk",
    "the nricx sensor reading is stale",
    "rpgtx is the codename for the release",
    "stamps and duty rosters for the week",
    "the customer loaned us a projector",
    "spacious room with a view",
    "spare keys are in the drawer",
    "a spatula and a saucepan",
    "duty manager on call tonight",
    "stamp the parcel before shipping",
    "legalese is not the topic here",
    "the contractual style guide is attached",
    "the spanish class starts at eight",
    "legally_blonde is a movie title",
    "spa_day is a calendar tag",
    "loanwords from malay in english",
    "agree on the meeting time",
    "the tenureship program launches next quarter",
)


def test_non_legal_corpus_has_at_least_thirty_prompts():
    assert len(NON_LEGAL_CORPUS) >= 30
    assert len(set(NON_LEGAL_CORPUS)) == len(NON_LEGAL_CORPUS)


@pytest.mark.parametrize("prompt", NON_LEGAL_CORPUS)
def test_non_legal_prompt_is_not_floored(prompt: str):
    jm = JudgmentModel()
    assert jm._contains_legal_terms(prompt.lower()) is False
    d = jm.rules_decide(JudgmentRequest("chat", prompt))
    assert d.tier == Tier.T1
    assert d.floor is None
    assert d.reason == "heuristic routing fallback"


@pytest.mark.parametrize("prompt", NON_LEGAL_CORPUS)
def test_non_legal_prompt_in_t1_node_is_served_at_t1(prompt: str):
    spy = SpyAdapter()
    router = _router(spy)
    routed = router.route(ModelRequest(request_type="chat", prompt=prompt, default_tier=Tier.T1, max_tier=Tier.T1))
    assert routed.tier == Tier.T1
    assert routed.adapter is spy
    assert routed.model == router.tier_models[Tier.T1]


@pytest.mark.parametrize("prompt", NON_LEGAL_CORPUS)
def test_non_legal_prompt_in_uncapped_node_is_t1(prompt: str):
    """Substring matching used to serve these at T2. Whole-word matching keeps T1."""
    spy = SpyAdapter()
    router = _router(spy)
    routed = router.route(ModelRequest(request_type="chat", prompt=prompt, default_tier=Tier.T1, max_tier=Tier.T3))
    assert routed.tier == Tier.T1


# --- Word-boundary matching: legal terms still floor -------------------------------------

LEGAL_CORPUS: tuple[str, ...] = (
    "review this loan agreement",
    "Review this Loan Agreement",
    "REVIEW THE SPA BEFORE SIGNING",
    "the spa is ready for signature",
    "what is the stamp duty on this",
    "stamp   duty computation",
    "is this legal?",
    "check the contract, please",
    "these contracts need a review",
    "the tenure is 30 years",
    "please verify the NRIC",
    "rpgt applies to the sale",
    "sub-contract terms",
    "loan: RM 500k",
    "(agreement) attached",
)


@pytest.mark.parametrize("prompt", LEGAL_CORPUS)
def test_legal_prompt_is_floored_to_t2(prompt: str):
    jm = JudgmentModel()
    d = jm.rules_decide(JudgmentRequest("chat", prompt))
    assert d.tier == Tier.T2
    assert d.floor == "legal/financial floor"


@pytest.mark.parametrize("term", JudgmentModel.LEGAL_TERMS)
def test_every_legal_term_still_matches_as_a_whole_word(term: str):
    jm = JudgmentModel()
    assert jm._contains_legal_terms(f"please check the {term} for me")
    assert jm._contains_legal_terms(f"PLEASE CHECK THE {term.upper()} FOR ME".lower())
    # Glued to a following word it is no longer the term.
    assert not jm._contains_legal_terms(f"please check the {term}ology for me")
    assert not jm._contains_legal_terms(f"please check the x{term} for me")


# --- Cost pre-gate does not fail differently on a refused node ------------------------


def test_estimate_node_cost_for_refused_node_is_zero():
    spy = SpyAdapter()
    router = _router(spy)
    dag = _one_node_dag("c1", "review this loan agreement", max_tier="T1")
    node = next(n for n in dag.nodes if n.id == "c1")  # type: ignore[attr-defined]
    assert estimate_node_cost(node, router, ExecutionContext("run_clamp_cost")) == 0.0


def test_refusal_error_names_floor_judged_tier_and_cap():
    exc = TierFloorAboveCap(request_type="chat", floor="legal/financial floor", judged_tier=Tier.T2, max_tier=Tier.T1)
    assert str(exc) == (
        "legal/financial floor requires T2 for request 'chat' but the node caps max_tier at T1; "
        "refusing to serve below the floor"
    )
