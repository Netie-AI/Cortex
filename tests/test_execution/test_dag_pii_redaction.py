"""GH-01 (#241): PII is redacted at the routed-completion choke-point.

Asserts the user-visible artifacts named in the issue: the AdapterRequest the
adapter receives, the DAG run result's node output, and the cost ledger rows in
``records_for_run``. The port module must not reach into ``packs.*``.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest
from netie.execution.dag_runner import ExecutionContext, run_dag
from netie.execution.executor import invoke_routed_completion
from netie.execution.model_router import BIG_API_PLACEHOLDER, ModelRequest, ModelRouter
from netie.fabrication.dsl_parser import parse_dsl
from netie.result import Ok
from netie.routing.adapters.base import AdapterRequest, AdapterResponse, LLMAdapter
from netie.routing.cost_ledger import CostLedger
from netie.routing.tiers import Tier
from netie.security import redact_port
from netie.security.redact_port import (
    RedactionFailed,
    clear_redactor,
    default_redact,
    register_redactor,
    registered_redactor,
)

RAW_NRIC = "S1234567D"
RAW_MYKAD = "900101-14-5678"
RAW_MYKAD_NODASH = "900101145678"
RAW_EMAIL = "jane@example.com"
RAW_CARD = "4111 1111 1111 1111"
PLAIN = "qty 12"


class SpyAdapter(LLMAdapter):
    """Records every AdapterRequest it receives and echoes the prompt back."""

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


def _router(spy: SpyAdapter) -> ModelRouter:
    return ModelRouter(
        adapter_registry={"anthropic": spy, "openai": spy, "self_hosted": spy},
        provider_aliases={BIG_API_PLACEHOLDER: "anthropic"},
    )


def _parse(dag: dict) -> object:
    r = parse_dsl(json.dumps(dag), "test")
    assert isinstance(r, Ok)
    return r.value


@pytest.fixture(autouse=True)
def _default_redactor():
    clear_redactor()
    yield
    clear_redactor()


def _pii_dag(prompt: str, system: str) -> object:
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
                    "default_tier": "T3",
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


# ---------------------------------------------------------------------------
# Real run_dag with an llm_judged node whose context carries PII
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_dag_llm_judged_redacts_context_pii_in_request_and_output():
    spy = SpyAdapter()
    ledger = CostLedger()
    ctx = ExecutionContext(
        "run_pii", seed={"customer": f"{RAW_NRIC} / {RAW_MYKAD} / {RAW_EMAIL} ordered {PLAIN}"}
    )
    dag = _pii_dag(
        prompt="Review customer record: {customer}",
        system=f"Operator on duty: {RAW_EMAIL}",
    )

    res = await run_dag(dag, ctx, _router(spy), ledger, workflow_cost_ceiling_myr=None)

    # The adapter request is the artifact that would leave the process.
    assert len(spy.requests) == 1
    req = spy.requests[0]
    for raw in (RAW_NRIC, RAW_MYKAD, RAW_EMAIL):
        assert raw not in req.prompt
        assert raw not in req.system
    assert "[REDACTED:nric]" in req.prompt
    assert "[REDACTED:mykad]" in req.prompt
    assert "[REDACTED:email]" in req.prompt
    assert "[REDACTED:email]" in req.system
    assert PLAIN in req.prompt

    # The run result the caller reads carries the placeholders, not the raw values.
    out = res.outputs["j1"].output
    content = out["content"]
    for raw in (RAW_NRIC, RAW_MYKAD, RAW_EMAIL):
        assert raw not in content
        assert raw not in out["raw"]["system_seen"]
    assert "[REDACTED:nric]" in content
    assert "[REDACTED:mykad]" in content
    assert "[REDACTED:email]" in content
    assert PLAIN in content

    # And the ledger row for the node is an ordinary ok row.
    rows = ledger.records_for_run("run_pii")
    j1 = [r for r in rows if r.node_id == "j1"]
    assert len(j1) == 1
    assert j1[0].status == "ok"
    assert j1[0].tier != "T0"


@pytest.mark.asyncio
async def test_run_dag_plain_prompt_is_unchanged():
    spy = SpyAdapter()
    ledger = CostLedger()
    ctx = ExecutionContext("run_plain", seed={"customer": f"{PLAIN} of SKU-BETA at WH-A, severity HIGH"})
    dag = _pii_dag(prompt="Review: {customer}", system="You are a reviewer.")

    res = await run_dag(dag, ctx, _router(spy), ledger, workflow_cost_ceiling_myr=None)

    assert spy.requests[0].prompt == f"Review: {PLAIN} of SKU-BETA at WH-A, severity HIGH"
    assert spy.requests[0].system == "You are a reviewer."
    assert res.outputs["j1"].output["content"].endswith(f"{PLAIN} of SKU-BETA at WH-A, severity HIGH")
    assert "[REDACTED:" not in res.outputs["j1"].output["content"]


# ---------------------------------------------------------------------------
# Choke-point directly: invoke_routed_completion
# ---------------------------------------------------------------------------


def _model_req(prompt: str) -> ModelRequest:
    return ModelRequest(
        request_type="plain",
        prompt=prompt,
        default_tier=Tier.T3,
        max_tier=Tier.T3,
        provider=BIG_API_PLACEHOLDER,
        cost_ceiling_myr=10.0,
    )


@pytest.mark.asyncio
async def test_invoke_routed_completion_redacts_prompt_and_system_before_adapter():
    spy = SpyAdapter()
    ledger = CostLedger()
    raw_prompt = f"card {RAW_CARD}, nric {RAW_NRIC}, mykad {RAW_MYKAD_NODASH}, {PLAIN}"
    outcome = await invoke_routed_completion(
        _router(spy),
        ledger,
        run_id="run_direct",
        workflow_cost_ceiling_myr=50.0,
        node_id="n1",
        model_req=_model_req(raw_prompt),
        adapter_req=AdapterRequest(model="", system=f"contact {RAW_EMAIL}", prompt=raw_prompt, max_tokens=50),
    )
    req = spy.requests[0]
    assert req.prompt == f"card [REDACTED:credit_card], nric [REDACTED:nric], mykad [REDACTED:mykad], {PLAIN}"
    assert req.system == "contact [REDACTED:email]"
    assert RAW_EMAIL not in outcome.response.raw["system_seen"]
    assert RAW_NRIC not in outcome.response.content
    assert ledger.records_for_run("run_direct")[0].status == "ok"


@pytest.mark.asyncio
async def test_raising_registered_redactor_aborts_before_adapter_and_records_error():
    spy = SpyAdapter()
    ledger = CostLedger()

    def broken(text: str) -> str:
        raise ValueError("ner backend unavailable")

    register_redactor(broken)
    assert registered_redactor() is broken

    with pytest.raises(RedactionFailed) as exc_info:
        await invoke_routed_completion(
            _router(spy),
            ledger,
            run_id="run_broken",
            workflow_cost_ceiling_myr=50.0,
            node_id="n_err",
            model_req=_model_req(f"nric {RAW_NRIC}"),
            adapter_req=AdapterRequest(model="", system="", prompt=f"nric {RAW_NRIC}", max_tokens=50),
        )
    assert isinstance(exc_info.value.__cause__, ValueError)

    # Zero adapter calls: nothing left the process.
    assert spy.requests == []

    rows = ledger.records_for_run("run_broken")
    assert len(rows) == 1
    assert rows[0].node_id == "n_err"
    assert rows[0].status == "error"
    assert rows[0].cost_myr == 0.0
    assert "ner backend unavailable" in (rows[0].error or "")
    assert RAW_NRIC not in (rows[0].error or "")


@pytest.mark.asyncio
async def test_raising_registered_redactor_surfaces_through_run_dag():
    spy = SpyAdapter()
    ledger = CostLedger()
    ctx = ExecutionContext("run_dag_broken", seed={"customer": RAW_NRIC})
    dag = _pii_dag(prompt="Review {customer}", system="")

    def broken(text: str) -> str:
        raise RuntimeError("boom")

    register_redactor(broken)
    with pytest.raises(RedactionFailed):
        await run_dag(dag, ctx, _router(spy), ledger, workflow_cost_ceiling_myr=None)

    assert spy.requests == []
    rows = [r for r in ledger.records_for_run("run_dag_broken") if r.node_id == "j1"]
    assert [r.status for r in rows] == ["error"]


@pytest.mark.asyncio
async def test_registered_redactor_replaces_engine_default():
    spy = SpyAdapter()
    ledger = CostLedger()
    seen: list[str] = []

    def pack_redactor(text: str) -> str:
        seen.append(text)
        return default_redact(text).replace("91234567", "[REDACTED:phone]")

    register_redactor(pack_redactor)
    await invoke_routed_completion(
        _router(spy),
        ledger,
        run_id="run_pack",
        workflow_cost_ceiling_myr=50.0,
        node_id="n1",
        model_req=_model_req("x"),
        adapter_req=AdapterRequest(model="", system="sys", prompt=f"call 91234567 re {RAW_EMAIL}", max_tokens=50),
    )
    assert seen == ["sys", f"call 91234567 re {RAW_EMAIL}"]
    assert spy.requests[0].prompt == "call [REDACTED:phone] re [REDACTED:email]"


@pytest.mark.asyncio
async def test_redactor_returning_non_string_fails_closed():
    spy = SpyAdapter()
    ledger = CostLedger()
    register_redactor(lambda text: None)  # type: ignore[arg-type, return-value]
    with pytest.raises(RedactionFailed):
        await invoke_routed_completion(
            _router(spy),
            ledger,
            run_id="run_none",
            workflow_cost_ceiling_myr=50.0,
            node_id="n1",
            model_req=_model_req("x"),
            adapter_req=AdapterRequest(model="", system="", prompt=RAW_NRIC, max_tokens=50),
        )
    assert spy.requests == []
    assert ledger.records_for_run("run_none")[0].status == "error"


@pytest.mark.asyncio
async def test_t0_tier_skips_adapter_and_never_needs_redaction():
    """T0 never calls the adapter, so a raising redactor must not be consulted."""
    spy = SpyAdapter()
    ledger = CostLedger()

    def broken(text: str) -> str:
        raise RuntimeError("must not run at T0")

    register_redactor(broken)
    outcome = await invoke_routed_completion(
        _router(spy),
        ledger,
        run_id="run_t0",
        workflow_cost_ceiling_myr=50.0,
        node_id="n0",
        model_req=ModelRequest(
            request_type="plain",
            prompt="hi",
            default_tier=Tier.T0,
            max_tier=Tier.T0,
            provider=BIG_API_PLACEHOLDER,
            cost_ceiling_myr=10.0,
        ),
        adapter_req=AdapterRequest(model="", system="", prompt=RAW_NRIC, max_tokens=50),
    )
    assert outcome.tier == "T0"
    assert spy.requests == []
    assert outcome.response.raw["skipped_adapter"] is True


# ---------------------------------------------------------------------------
# Engine default redactor patterns
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (f"nric {RAW_NRIC}", "nric [REDACTED:nric]"),
        ("fin g7654321x", "fin [REDACTED:nric]"),
        (f"mykad {RAW_MYKAD}", "mykad [REDACTED:mykad]"),
        (f"mykad {RAW_MYKAD_NODASH}", "mykad [REDACTED:mykad]"),
        (f"mail {RAW_EMAIL}", "mail [REDACTED:email]"),
        (f"card {RAW_CARD}", "card [REDACTED:credit_card]"),
        ("card 4111111111111111", "card [REDACTED:credit_card]"),
        (PLAIN, PLAIN),
        ("Which SKUs are below reorder level in warehouse WH-A?", "Which SKUs are below reorder level in warehouse WH-A?"),
        ("order 2026-09-23 total 1234.56", "order 2026-09-23 total 1234.56"),
        ("phone 91234567", "phone 91234567"),
        ("ref 9001011456789", "ref [REDACTED:credit_card]"),
    ],
)
def test_default_redact_patterns(text: str, expected: str) -> None:
    assert default_redact(text) == expected


def test_default_is_active_when_nothing_registered() -> None:
    clear_redactor()
    assert registered_redactor() is None
    assert redact_port.active_redactor() is default_redact
    assert redact_port.redact_prompt_text(f"{RAW_NRIC} {PLAIN}") == f"[REDACTED:nric] {PLAIN}"


# ---------------------------------------------------------------------------
# Pack reuses the engine patterns and keeps its API; boundary holds
# ---------------------------------------------------------------------------


def test_pack_pii_reuses_engine_patterns_and_adds_mykad() -> None:
    from packs.dms.security import pii

    out = pii.redact_for_prompt(f"{RAW_NRIC} {RAW_MYKAD} {RAW_EMAIL} 91234567 {PLAIN}")
    assert out == "[REDACTED:nric] [REDACTED:mykad] [REDACTED:email] [REDACTED:phone] qty 12"
    kinds = [s.kind for s in pii.detect(f"{RAW_NRIC} {RAW_MYKAD}")]
    assert kinds == ["nric", "mykad"]
    assert pii._NRIC is redact_port.NRIC
    assert pii._MYKAD is redact_port.MYKAD
    assert pii._EMAIL is redact_port.EMAIL
    assert pii._CREDIT_CARD is redact_port.CREDIT_CARD


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
