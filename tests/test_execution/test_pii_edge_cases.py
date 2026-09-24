"""H2-PII-EDGE (#250): redactor edge cases, and redaction before the router sees the prompt.

Two residuals of GH-01 (#241), both verified in ``2026-09-23_gh-01.md``:

1. ``default_redact`` used ``\\b`` for the NRIC, which does not break at ``_``,
   the MyKad accepted no spaces, and fullwidth digits/letters were invisible to
   the ASCII patterns.
2. ``invoke_routed_completion`` called ``router.route(model_req)`` before it
   redacted anything, so ``JudgmentModel.decide`` and any decision backend
   behind it (kev over HTTP included) received the raw prompt.

Every test asserts a user-visible artifact: the ``AdapterRequest`` the spy
adapter received, the ``JudgmentRequest`` and backend ``state`` a spy judgment
model saw, the JSON body a kev backend POSTed, the DAG run output, or the cost
ledger rows. Redactor-level assertions are in addition, never instead.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from netie.decision.backends import KevHttpBackend
from netie.decision.models import ChoiceQuestion, Question, RawDecision
from netie.execution.dag_runner import ExecutionContext, run_dag
from netie.execution.executor import invoke_routed_completion
from netie.execution.model_router import BIG_API_PLACEHOLDER, ModelRequest, ModelRouter
from netie.fabrication.dsl_parser import parse_dsl
from netie.result import Ok
from netie.routing.adapters.base import AdapterRequest, AdapterResponse, LLMAdapter
from netie.routing.cost_ledger import CostLedger
from netie.routing.judgment_model import JudgmentDecision, JudgmentModel, JudgmentRequest
from netie.routing.tiers import Tier
from netie.security.redact_port import (
    RedactionFailed,
    clear_redactor,
    default_redact,
    detect_spans,
    register_redactor,
)

# --- Edge cases the GH-01 verifier found unredacted ----------------------------

EDGE_CASES: tuple[tuple[str, str, str], ...] = (
    # (id, raw text, expected redacted text)
    ("nric_trailing_underscore", "S1234567D_x", "[REDACTED:nric]_x"),
    ("nric_leading_underscore", "user_S1234567D", "user_[REDACTED:nric]"),
    ("nric_both_underscores", "id_S1234567D_v2", "id_[REDACTED:nric]_v2"),
    ("nric_glued_to_letters", "icS1234567Dend", "ic[REDACTED:nric]end"),
    ("fin_leading_underscore", "acct_G7654321X", "acct_[REDACTED:nric]"),
    ("nric_lowercase_underscore", "user_s1234567d", "user_[REDACTED:nric]"),
    ("mykad_spaces", "900101 14 5678", "[REDACTED:mykad]"),
    ("mykad_spaces_in_sentence", "IC 900101 14 5678 on file", "IC [REDACTED:mykad] on file"),
    ("mykad_underscore", "ic_900101-14-5678", "ic_[REDACTED:mykad]"),
    ("mykad_glued_to_letters", "ic900101145678x", "ic[REDACTED:mykad]x"),
    ("mykad_dashes_underscore_after", "900101-14-5678_old", "[REDACTED:mykad]_old"),
    ("fullwidth_nric", "Ｓ１２３４５６７Ｄ", "[REDACTED:nric]"),
    ("fullwidth_nric_lower", "ｓ１２３４５６７ｄ", "[REDACTED:nric]"),
    ("fullwidth_mykad", "９００１０１−１４−５６７８".replace("−", "-"), "[REDACTED:mykad]"),
    ("fullwidth_mykad_nodash", "９００１０１１４５６７８", "[REDACTED:mykad]"),
    ("fullwidth_mixed_ascii", "nric Ｓ1234567Ｄ ok", "nric [REDACTED:nric] ok"),
    ("fullwidth_email", "ｊａｎｅ＠ｅｘａｍｐｌｅ．ｃｏｍ", "[REDACTED:email]"),
    ("fullwidth_card", "４１１１ １１１１ １１１１ １１１１", "[REDACTED:credit_card]"),
)

# --- Legitimate analytics text that must come out byte-identical ---------------

NON_PII_CORPUS: tuple[str, ...] = (
    "qty 12",
    "PO-2026-0001",
    "SO_2026_0042",
    "2026-09-24",
    "2026-09-24T10:00:00Z",
    "order 2026-09-23 total 1234.56",
    "Which SKUs are below reorder level in warehouse WH-A?",
    "SKU-BETA at WH-A, severity HIGH",
    "invoice INV-000123 due 30 days",
    "batch_1234567 shipped",
    "lot L1234567 expires 2027-01-31",
    "temperature -18.5 C at 06:00",
    "ref 12345678",
    "part no 123-456-7890",
    "grn GRN_20260924_01",
    "user_id 42 updated 3 rows",
    "S123 is a shelf code",
    "T12345678 is a tracking id",
    "900101-17-5678 is not a MyKad (place code 17 is unassigned)",
    "900101-14 5678 mixes separators",
    "1234567890 is a ten digit ref",
    "phone 91234567",
    "margin 12.5% on 1,234 units",
    "zone A1_B2_C3 count 7",
    "email support at example dot com",
    "版本 2.5.0 已发布",
    "Ｑ１ 销量 １２３",
    "",
    " ",
)

assert len(NON_PII_CORPUS) >= 20


class SpyAdapter(LLMAdapter):
    def __init__(self) -> None:
        self.requests: list[AdapterRequest] = []

    async def complete(self, req: AdapterRequest) -> AdapterResponse:
        self.requests.append(req)
        return AdapterResponse(
            content=f"echo: {req.prompt}",
            prompt_tokens=10,
            completion_tokens=5,
            latency_ms=1,
            raw={"system_seen": req.system},
        )

    def cost_myr(self, prompt_tokens: int, completion_tokens: int) -> float:
        return round(prompt_tokens * 0.001 + completion_tokens * 0.002, 6)


class SpyDecisionBackend:
    """A decision backend that records every ``state`` it is asked about and answers T1."""

    name = "spy-backend"

    def __init__(self, choice: Tier = Tier.T1) -> None:
        self.choice = choice
        self.states: list[dict[str, Any]] = []

    def evaluate(self, question: Question, state: Any) -> RawDecision:
        assert isinstance(question, ChoiceQuestion)
        self.states.append(dict(state))
        scores = tuple(0.97 if label == self.choice.value else 0.01 for label in question.labels)
        return RawDecision(scores=scores, is_logits=False, calibrated=True, backend=self.name)


class SpyJudgmentModel(JudgmentModel):
    """Records the exact ``JudgmentRequest`` the router hands to ``decide``."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.requests: list[JudgmentRequest] = []

    def decide(self, req: JudgmentRequest) -> JudgmentDecision:
        self.requests.append(req)
        return super().decide(req)


def _router(spy: SpyAdapter, jm: JudgmentModel | None = None) -> ModelRouter:
    return ModelRouter(
        judgment_model=jm,
        adapter_registry={"anthropic": spy, "openai": spy, "self_hosted": spy},
        provider_aliases={BIG_API_PLACEHOLDER: "anthropic"},
    )


def _parse(dag: dict) -> object:
    r = parse_dsl(json.dumps(dag), "test")
    assert isinstance(r, Ok)
    return r.value


def _pii_dag(prompt: str, system: str = "You are a reviewer.") -> object:
    return _parse(
        {
            "version": "2.0",
            "intent_hash": "h",
            "entry_node_id": "j1",
            "output_node_id": "e1",
            "nodes": [
                {
                    "id": "j1",
                    "kind": "llm_judged",
                    "default_tier": "T1",
                    "max_tier": "T3",
                    "provider": BIG_API_PLACEHOLDER,
                    "prompt": prompt,
                    "system": system,
                    "max_tokens": 50,
                    "cost_ceiling_myr": 10.0,
                    "inputs": [],
                },
                {"id": "e1", "kind": "EMIT", "tier": 0, "inputs": ["j1"]},
            ],
        }
    )


def _model_req(
    prompt: str, *, default_tier: Tier = Tier.T1, max_tier: Tier = Tier.T3, request_type: str = "plain"
) -> ModelRequest:
    return ModelRequest(
        request_type=request_type,
        prompt=prompt,
        default_tier=default_tier,
        max_tier=max_tier,
        provider=BIG_API_PLACEHOLDER,
        cost_ceiling_myr=10.0,
    )


@pytest.fixture(autouse=True)
def _default_redactor():
    clear_redactor()
    yield
    clear_redactor()


# ---------------------------------------------------------------------------
# 1. Edge cases through the engine default redactor
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("raw", "expected"), [(r, e) for _, r, e in EDGE_CASES], ids=[i for i, _, _ in EDGE_CASES])
def test_default_redact_edge_case(raw: str, expected: str) -> None:
    out = default_redact(raw)
    assert out == expected
    assert "[REDACTED:" in out


def test_fold_for_matching_preserves_length_and_indexes_original() -> None:
    from netie.security.redact_port import fold_for_matching  # absent on the pre-#250 module

    raw = "ﬁle Ｓ１２３４５６７Ｄ ﬁle"  # ligature expands under NFKC; must be left alone
    folded = fold_for_matching(raw)
    assert len(folded) == len(raw)
    assert folded == "ﬁle S1234567D ﬁle"
    spans = detect_spans(raw)
    assert [s.kind for s in spans] == ["nric"]
    assert raw[spans[0].start : spans[0].end] == "Ｓ１２３４５６７Ｄ"
    assert spans[0].text == "Ｓ１２３４５６７Ｄ"
    assert default_redact(raw) == "ﬁle [REDACTED:nric] ﬁle"


# ---------------------------------------------------------------------------
# 2. Each edge case through run_dag: adapter request, run output, ledger row
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(("raw", "expected"), [(r, e) for _, r, e in EDGE_CASES], ids=[i for i, _, _ in EDGE_CASES])
async def test_run_dag_redacts_edge_case_before_adapter(raw: str, expected: str) -> None:
    spy = SpyAdapter()
    ledger = CostLedger()
    ctx = ExecutionContext("run_edge", seed={"customer": raw})
    # dag_runner renders only the prompt template from context; the system is literal.
    dag = _pii_dag(prompt="Review customer record: {customer}", system=f"Operator note: {raw}")

    res = await run_dag(dag, ctx, _router(spy), ledger, workflow_cost_ceiling_myr=None)

    assert len(spy.requests) == 1
    req = spy.requests[0]
    assert req.prompt == f"Review customer record: {expected}"
    assert req.system == f"Operator note: {expected}"
    assert raw not in req.prompt
    assert raw not in req.system

    content = res.outputs["j1"].output["content"]
    assert content == f"echo: Review customer record: {expected}"
    assert raw not in res.outputs["j1"].output["raw"]["system_seen"]

    rows = [r for r in ledger.records_for_run("run_edge") if r.node_id == "j1"]
    assert [r.status for r in rows] == ["ok"]
    assert rows[0].tier != "T0"


# ---------------------------------------------------------------------------
# 3. Non-PII corpus comes out byte-identical (no false positives)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("text", NON_PII_CORPUS, ids=[f"corpus_{i}" for i in range(len(NON_PII_CORPUS))])
def test_default_redact_leaves_non_pii_byte_identical(text: str) -> None:
    out = default_redact(text)
    assert out == text
    assert out.encode("utf-8") == text.encode("utf-8")
    assert detect_spans(text) == []


@pytest.mark.asyncio
async def test_run_dag_passes_non_pii_corpus_byte_identical() -> None:
    spy = SpyAdapter()
    ledger = CostLedger()
    corpus = [t for t in NON_PII_CORPUS if t.strip()]
    joined = " | ".join(corpus)
    ctx = ExecutionContext("run_corpus", seed={"text": joined})
    dag = _pii_dag(prompt="{text}", system=joined)  # system is literal; prompt renders from context

    res = await run_dag(dag, ctx, _router(spy), ledger, workflow_cost_ceiling_myr=None)

    assert spy.requests[0].prompt == joined
    assert spy.requests[0].system == joined
    assert res.outputs["j1"].output["content"] == f"echo: {joined}"
    assert "[REDACTED:" not in res.outputs["j1"].output["content"]


# ---------------------------------------------------------------------------
# 4. The router (and any decision backend behind it) sees placeholders only
# ---------------------------------------------------------------------------

RAW_MIX = "S1234567D / user_S1234567D / 900101 14 5678 / Ｓ７６５４３２１Ｄ / jane@example.com qty 12"
RAW_VALUES = ("S1234567D", "900101 14 5678", "Ｓ７６５４３２１Ｄ", "jane@example.com")


@pytest.mark.asyncio
async def test_router_and_backend_receive_redacted_prompt() -> None:
    spy = SpyAdapter()
    backend = SpyDecisionBackend(Tier.T1)
    jm = SpyJudgmentModel(decision_backend=backend, abstain_threshold=0.0)
    ledger = CostLedger()

    outcome = await invoke_routed_completion(
        _router(spy, jm),
        ledger,
        run_id="run_route",
        workflow_cost_ceiling_myr=50.0,
        node_id="n1",
        model_req=_model_req(RAW_MIX),
        adapter_req=AdapterRequest(model="", system="contact jane@example.com", prompt=RAW_MIX, max_tokens=50),
    )

    # JudgmentRequest the router built.
    assert len(jm.requests) == 1
    seen = jm.requests[0].content
    for raw in RAW_VALUES:
        assert raw not in seen
    assert seen == "[REDACTED:nric] / user_[REDACTED:nric] / [REDACTED:mykad] / [REDACTED:nric] / [REDACTED:email] qty 12"
    assert jm.requests[0].context_size == len(seen)

    # State dict the decision backend evaluated (what kev would be POSTed).
    assert len(backend.states) >= 1
    for state in backend.states:
        assert state["content"] == seen
        for raw in RAW_VALUES:
            assert raw not in json.dumps(state, ensure_ascii=False)

    # Adapter request and run outcome carry the same placeholders.
    assert spy.requests[0].prompt == seen
    assert spy.requests[0].system == "contact [REDACTED:email]"
    assert "[REDACTED:nric]" in outcome.response.content
    assert "S1234567D" not in outcome.response.content
    assert ledger.records_for_run("run_route")[0].status == "ok"


@pytest.mark.asyncio
async def test_run_dag_backend_sees_only_placeholders() -> None:
    spy = SpyAdapter()
    backend = SpyDecisionBackend(Tier.T1)
    jm = SpyJudgmentModel(decision_backend=backend, abstain_threshold=0.0)
    ledger = CostLedger()
    ctx = ExecutionContext("run_dag_route", seed={"customer": RAW_MIX})
    dag = _pii_dag(prompt="Review: {customer}")

    res = await run_dag(dag, ctx, _router(spy, jm), ledger, workflow_cost_ceiling_myr=None)

    assert len(jm.requests) == 1
    assert len(backend.states) >= 1
    for raw in RAW_VALUES:
        assert raw not in jm.requests[0].content
        assert all(raw not in json.dumps(s, ensure_ascii=False) for s in backend.states)
    assert "[REDACTED:mykad]" in jm.requests[0].content
    assert jm.requests[0].content == spy.requests[0].prompt
    assert "S1234567D" not in res.outputs["j1"].output["content"]
    assert [r.status for r in ledger.records_for_run("run_dag_route") if r.node_id == "j1"] == ["ok"]


@pytest.mark.asyncio
async def test_kev_http_backend_posts_only_placeholders(monkeypatch: pytest.MonkeyPatch) -> None:
    """The wire artifact: the JSON body a KevHttpBackend POSTs to the loopback server."""
    posted: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        posted.append(json.loads(request.content.decode("utf-8")))
        probs = {"T0": 0.01, "T1": 0.97, "T2": 0.01, "T3": 0.01}
        return httpx.Response(200, json={"answers": {"q": {"probabilities": probs}}})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    backend = KevHttpBackend("http://127.0.0.1:9", client=client)
    jm = JudgmentModel(decision_backend=backend, abstain_threshold=0.0)
    spy = SpyAdapter()
    ledger = CostLedger()

    await invoke_routed_completion(
        _router(spy, jm),
        ledger,
        run_id="run_kev",
        workflow_cost_ceiling_myr=50.0,
        node_id="n1",
        model_req=_model_req(RAW_MIX),
        adapter_req=AdapterRequest(model="", system="", prompt=RAW_MIX, max_tokens=50),
    )

    assert posted, "kev backend was never asked"
    for body in posted:
        wire = json.dumps(body, ensure_ascii=False)
        for raw in RAW_VALUES:
            assert raw not in wire
        assert "[REDACTED:nric]" in body["state"]["content"]
        assert "[REDACTED:mykad]" in body["state"]["content"]
    assert spy.requests[0].prompt == posted[0]["state"]["content"]


@pytest.mark.asyncio
async def test_legal_floor_still_applies_on_redacted_text() -> None:
    """Placeholders do not remove legal words: a loan agreement with an NRIC lands at T2 even with a T0 backend."""
    spy = SpyAdapter()
    backend = SpyDecisionBackend(Tier.T0)
    jm = SpyJudgmentModel(decision_backend=backend, abstain_threshold=0.0)
    ledger = CostLedger()
    raw = "review this loan agreement for user_S1234567D"
    ctx = ExecutionContext("run_legal", seed={"customer": raw})
    dag = _pii_dag(prompt="{customer}")

    res = await run_dag(dag, ctx, _router(spy, jm), ledger, workflow_cost_ceiling_myr=None)

    assert jm.requests[0].content == "review this loan agreement for user_[REDACTED:nric]"
    rows = [r for r in ledger.records_for_run("run_legal") if r.node_id == "j1"]
    assert [r.tier for r in rows] == ["T2"]
    assert res.outputs["j1"].tier == "T2"
    assert spy.requests[0].model == _router(spy).tier_models[Tier.T2]
    assert "S1234567D" not in spy.requests[0].prompt


@pytest.mark.asyncio
async def test_nric_placeholder_alone_triggers_legal_floor() -> None:
    """``[REDACTED:nric]`` contains the legal term ``nric``: an identity number is routed at least T2."""
    spy = SpyAdapter()
    jm = SpyJudgmentModel()
    ledger = CostLedger()
    outcome = await invoke_routed_completion(
        _router(spy, jm),
        ledger,
        run_id="run_nric_floor",
        workflow_cost_ceiling_myr=50.0,
        node_id="n1",
        model_req=_model_req("summarise record S1234567D"),
        adapter_req=AdapterRequest(model="", system="", prompt="summarise record S1234567D", max_tokens=50),
    )
    assert jm.requests[0].content == "summarise record [REDACTED:nric]"
    assert outcome.tier == "T2"
    assert ledger.records_for_run("run_nric_floor")[0].tier == "T2"


# ---------------------------------------------------------------------------
# 5. Fail closed: a raising redactor never reaches the router
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_raising_redactor_never_reaches_router_or_backend() -> None:
    spy = SpyAdapter()
    backend = SpyDecisionBackend(Tier.T1)
    jm = SpyJudgmentModel(decision_backend=backend, abstain_threshold=0.0)
    ledger = CostLedger()

    def broken(text: str) -> str:
        raise RuntimeError("ner backend unavailable")

    register_redactor(broken)
    with pytest.raises(RedactionFailed):
        await invoke_routed_completion(
            _router(spy, jm),
            ledger,
            run_id="run_broken",
            workflow_cost_ceiling_myr=50.0,
            node_id="n_err",
            model_req=_model_req("S1234567D"),
            adapter_req=AdapterRequest(model="", system="", prompt="S1234567D", max_tokens=50),
        )

    assert jm.requests == []
    assert backend.states == []
    assert spy.requests == []
    rows = ledger.records_for_run("run_broken")
    assert len(rows) == 1
    assert rows[0].status == "error"
    assert rows[0].cost_myr == 0.0
    assert rows[0].tier == Tier.T1.value  # the request's default tier; nothing was routed
    assert "S1234567D" not in (rows[0].error or "")


@pytest.mark.asyncio
async def test_max_tier_t0_with_raising_redactor_routes_on_empty_prompt() -> None:
    """max_tier T0 can never call a model; the router still must not see raw text."""
    spy = SpyAdapter()
    backend = SpyDecisionBackend(Tier.T0)
    jm = SpyJudgmentModel(decision_backend=backend, abstain_threshold=0.0)
    ledger = CostLedger()

    def broken(text: str) -> str:
        raise RuntimeError("must not leak")

    register_redactor(broken)
    outcome = await invoke_routed_completion(
        _router(spy, jm),
        ledger,
        run_id="run_t0",
        workflow_cost_ceiling_myr=50.0,
        node_id="n0",
        model_req=_model_req("S1234567D", default_tier=Tier.T0, max_tier=Tier.T0),
        adapter_req=AdapterRequest(model="", system="", prompt="S1234567D", max_tokens=50),
    )
    assert outcome.tier == "T0"
    assert spy.requests == []
    assert [r.content for r in jm.requests] == [""]
    assert all("S1234567D" not in json.dumps(s) for s in backend.states)
    assert ledger.records_for_run("run_t0")[0].status == "ok"


@pytest.mark.asyncio
async def test_registered_redactor_output_is_what_the_router_sees() -> None:
    spy = SpyAdapter()
    jm = SpyJudgmentModel()
    ledger = CostLedger()

    def pack_redactor(text: str) -> str:
        return default_redact(text).replace("91234567", "[REDACTED:phone]")

    register_redactor(pack_redactor)
    await invoke_routed_completion(
        _router(spy, jm),
        ledger,
        run_id="run_pack",
        workflow_cost_ceiling_myr=50.0,
        node_id="n1",
        model_req=_model_req("call 91234567 re user_S1234567D"),
        adapter_req=AdapterRequest(model="", system="", prompt="call 91234567 re user_S1234567D", max_tokens=50),
    )
    assert jm.requests[0].content == "call [REDACTED:phone] re user_[REDACTED:nric]"
    assert spy.requests[0].prompt == jm.requests[0].content


# ---------------------------------------------------------------------------
# 6. Boundary: the redactor stays engine-owned
# ---------------------------------------------------------------------------


def test_redact_port_and_executor_import_no_packs() -> None:
    root = Path(__file__).resolve().parents[2]
    for rel in ("CortexOS/security/redact_port.py", "CortexOS/execution/executor.py"):
        tree = ast.parse((root / rel).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            else:
                continue
            for name in names:
                assert not (name == "packs" or name.startswith("packs.")), f"{rel} imports {name}"
