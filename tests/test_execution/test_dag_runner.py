import json

import pytest

from netie.execution.dag_runner import ExecutionContext, run_dag
from netie.execution.errors import OutboundNotSendable, WorkflowCostCeilingExceeded
from netie.execution.model_router import BIG_API_PLACEHOLDER, ModelRouter
from netie.fabrication.dsl_parser import parse_dsl
from netie.result import Ok
from netie.routing.adapters.base import LLMAdapter
from netie.routing.cost_ledger import CostLedger

from tests.test_execution.test_cost_ledger_and_executor import StubAdapter


def _stub_registry(rate: float = 0.0) -> dict[str, LLMAdapter]:
    stub = StubAdapter(projected_rate=rate)
    return {"anthropic": stub, "openai": stub, "self_hosted": stub}


def _parse(dag: dict) -> object:
    r = parse_dsl(json.dumps(dag), "test")
    assert isinstance(r, Ok)
    return r.value


@pytest.mark.asyncio
async def test_llm_judged_node_calls_router_and_writes_ledger():
    dag = _parse(
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
                    "prompt": "spa legal clause check",
                    "max_tokens": 50,
                    "cost_ceiling_myr": 10.0,
                    "inputs": [],
                },
                {"id": "e1", "kind": "EMIT", "tier": 0, "inputs": ["j1"]},
            ],
        }
    )
    router = ModelRouter(
        adapter_registry=_stub_registry(),
        provider_aliases={BIG_API_PLACEHOLDER: "anthropic"},
    )
    ledger = CostLedger()
    ctx = ExecutionContext("run_judge")
    res = await run_dag(dag, ctx, router, ledger, workflow_cost_ceiling_myr=None)
    assert "j1" in res.outputs
    recs = ledger.records()
    assert len(recs) == 1
    assert recs[0].status == "ok"
    assert recs[0].cost_myr > 0
    assert recs[0].tier in {"T1", "T2", "T3"}


@pytest.mark.asyncio
async def test_workflow_ceiling_halts_before_node_3():
    per_node = 0.1
    dag = _parse(
        {
            "version": "2.0",
            "intent_hash": "h",
            "entry_node_id": "a",
            "output_node_id": "emit",
            "nodes": [
                {
                    "id": "a",
                    "kind": "llm_judged",
                    "default_tier": "T3",
                    "max_tier": "T3",
                    "provider": BIG_API_PLACEHOLDER,
                    "prompt": "x",
                    "max_tokens": 10,
                    "cost_ceiling_myr": 10.0,
                    "inputs": [],
                },
                {
                    "id": "b",
                    "kind": "llm_judged",
                    "default_tier": "T3",
                    "max_tier": "T3",
                    "provider": BIG_API_PLACEHOLDER,
                    "prompt": "{a}",
                    "max_tokens": 10,
                    "cost_ceiling_myr": 10.0,
                    "inputs": ["a"],
                },
                {
                    "id": "c",
                    "kind": "llm_judged",
                    "default_tier": "T3",
                    "max_tier": "T3",
                    "provider": BIG_API_PLACEHOLDER,
                    "prompt": "{b}",
                    "max_tokens": 10,
                    "cost_ceiling_myr": 10.0,
                    "inputs": ["b"],
                },
                {"id": "emit", "kind": "EMIT", "tier": 0, "inputs": ["c"]},
            ],
        }
    )
    router = ModelRouter(
        adapter_registry=_stub_registry(rate=per_node),
        provider_aliases={BIG_API_PLACEHOLDER: "anthropic"},
    )
    ledger = CostLedger()
    ctx = ExecutionContext("run_cap")
    ceiling = 2 * per_node
    with pytest.raises(WorkflowCostCeilingExceeded):
        await run_dag(dag, ctx, router, ledger, workflow_cost_ceiling_myr=ceiling)
    assert len(ledger.records()) == 2


@pytest.mark.asyncio
async def test_deterministic_rule_catches_missing_nric():
    dag = _parse(
        {
            "version": "2.0",
            "intent_hash": "h",
            "entry_node_id": "dref",
            "output_node_id": "out",
            "nodes": [
                {
                    "id": "dref",
                    "kind": "document_ref",
                    "context_key": "seed_doc",
                    "inputs": [],
                },
                {
                    "id": "chk",
                    "kind": "deterministic_rule",
                    "ruleset": "spa_v1.yaml",
                    "inputs": ["dref"],
                },
                {"id": "out", "kind": "EMIT", "tier": 0, "inputs": ["chk"]},
            ],
        }
    )
    router = ModelRouter(adapter_registry=_stub_registry())
    ledger = CostLedger()
    seed = {"doc_type": "spa", "price": 500_000}
    ctx = ExecutionContext("run_comp", seed={"seed_doc": seed})
    res = await run_dag(dag, ctx, router, ledger, workflow_cost_ceiling_myr=None)
    assert ledger.records() == []
    assert res.outputs["chk"].output["violations"] == ["spa_must_have_buyer_nric"]


@pytest.mark.asyncio
async def test_deterministic_rule_stamp_duty_miscalc():
    dag = _parse(
        {
            "version": "2.0",
            "intent_hash": "h",
            "entry_node_id": "dref",
            "output_node_id": "out",
            "nodes": [
                {
                    "id": "dref",
                    "kind": "document_ref",
                    "context_key": "seed_doc",
                    "inputs": [],
                },
                {
                    "id": "chk",
                    "kind": "deterministic_rule",
                    "ruleset": "spa_v1.yaml",
                    "inputs": ["dref"],
                },
                {"id": "out", "kind": "EMIT", "tier": 0, "inputs": ["chk"]},
            ],
        }
    )
    router = ModelRouter(adapter_registry=_stub_registry())
    ledger = CostLedger()
    seed = {
        "doc_type": "spa",
        "buyer_nric": "900101-01-5678",
        "price": 1_000_000,
        "stamp_duty_stated_myr": 9_500.0,
        "stamp_duty_computed_myr": 10_000.0,
    }
    ctx = ExecutionContext("run_stamp", seed={"seed_doc": seed})
    res = await run_dag(dag, ctx, router, ledger, workflow_cost_ceiling_myr=None)
    assert ledger.records() == []
    assert "stamp_duty_calculation" in res.outputs["chk"].output["violations"]


@pytest.mark.asyncio
async def test_outbound_node_blocked_when_not_sendable(monkeypatch):
    dag = _parse(
        {
            "version": "2.0",
            "intent_hash": "h",
            "entry_node_id": "o1",
            "output_node_id": "e1",
            "nodes": [
                {
                    "id": "o1",
                    "kind": "llm_judged",
                    "outbound": True,
                    "default_tier": "T3",
                    "max_tier": "T3",
                    "provider": BIG_API_PLACEHOLDER,
                    "prompt": "ping",
                    "max_tokens": 20,
                    "cost_ceiling_myr": 10.0,
                    "inputs": [],
                },
                {"id": "e1", "kind": "EMIT", "tier": 0, "inputs": ["o1"]},
            ],
        }
    )
    router = ModelRouter(
        adapter_registry=_stub_registry(),
        provider_aliases={BIG_API_PLACEHOLDER: "anthropic"},
    )
    ledger = CostLedger()
    ctx = ExecutionContext("run_out", {"user_tz": "Asia/Kuala_Lumpur", "message_urgency": "normal"})
    monkeypatch.setattr("netie.execution.dag_runner.is_sendable_now", lambda *a, **k: False)
    with pytest.raises(OutboundNotSendable):
        await run_dag(dag, ctx, router, ledger, workflow_cost_ceiling_myr=None)
    assert ledger.records() == []


def test_bumi_rule_only_when_applicable():
    from netie.compliance.engine import ComplianceEngine

    eng = ComplianceEngine.from_ruleset("spa_v1.yaml")
    assert eng.check({"doc_type": "spa", "buyer_nric": "x", "price": 1}) == []
    hits = eng.check(
        {
            "doc_type": "spa",
            "buyer_nric": "x",
            "price": 1,
            "tenure": "bumi_lot",
            "buyer_race": "chinese",
        }
    )
    assert "bumi_lot_disclosure" in hits
