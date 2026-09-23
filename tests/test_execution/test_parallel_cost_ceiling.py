"""GH-03 (#243): cumulative cost gate for parallel DAG batches.

Three sibling ``llm_judged`` nodes, each priced at 1.0 MYR by a spy adapter.
The assertions are on the user-visible artifacts: the run result (raises or
completes with outputs), the adapter call log (nothing spent when refused) and
the cost ledger rows for the run.
"""

from __future__ import annotations

import asyncio
import json

import pytest
from netie.execution.dag_runner import ExecutionContext, run_dag
from netie.execution.errors import CostCeilingExceeded, WorkflowCostCeilingExceeded
from netie.execution.model_router import BIG_API_PLACEHOLDER, ModelRouter
from netie.fabrication.dsl_parser import parse_dsl
from netie.result import Ok
from netie.routing.adapters.base import AdapterResponse, LLMAdapter
from netie.routing.cost_ledger import CostLedger

PER_NODE_MYR = 1.0
SIBLINGS = ("s1", "s2", "s3")


class SpyAdapter(LLMAdapter):
    """Prices every projection and every actual call at ``rate`` MYR and records calls.

    ``complete`` yields to the event loop once, like a real network adapter, so
    siblings under ``asyncio.gather`` are genuinely in flight together and the
    per-call ledger check inside ``invoke_routed_completion`` sees the
    pre-batch total for every one of them.
    """

    def __init__(self, rate: float) -> None:
        self.rate = rate
        self.calls: list[str] = []

    async def complete(self, req) -> AdapterResponse:  # type: ignore[no-untyped-def]
        self.calls.append(req.prompt)
        await asyncio.sleep(0)
        return AdapterResponse(content="ok", prompt_tokens=10, completion_tokens=20, latency_ms=1, raw={})

    def cost_myr(self, prompt_tokens: int, completion_tokens: int) -> float:
        del prompt_tokens, completion_tokens
        return round(self.rate, 6)


def _three_sibling_dag():
    nodes = [
        {
            "id": nid,
            "kind": "llm_judged",
            "default_tier": "T3",
            "max_tier": "T3",
            "provider": BIG_API_PLACEHOLDER,
            "prompt": f"sibling {nid}",
            "max_tokens": 10,
            "cost_ceiling_myr": 10.0,
            "inputs": [],
        }
        for nid in SIBLINGS
    ]
    nodes.append({"id": "emit", "kind": "EMIT", "tier": 0, "inputs": list(SIBLINGS)})
    r = parse_dsl(
        json.dumps(
            {
                "version": "2.0",
                "intent_hash": "h",
                "entry_node_id": "s1",
                "output_node_id": "emit",
                "nodes": nodes,
            }
        ),
        "test",
    )
    assert isinstance(r, Ok)
    return r.value


def _setup() -> tuple[SpyAdapter, ModelRouter, CostLedger]:
    spy = SpyAdapter(PER_NODE_MYR)
    router = ModelRouter(
        adapter_registry={"anthropic": spy, "openai": spy, "self_hosted": spy},
        provider_aliases={BIG_API_PLACEHOLDER: "anthropic"},
    )
    return spy, router, CostLedger()


@pytest.mark.asyncio
async def test_parallel_batch_refused_before_any_sibling_spends():
    """Ceiling 2.0, three 1.0 siblings in one parallel batch: refused up front."""
    spy, router, ledger = _setup()
    ctx = ExecutionContext("run_par_refuse")

    with pytest.raises(WorkflowCostCeilingExceeded) as excinfo:
        await run_dag(
            _three_sibling_dag(), ctx, router, ledger, workflow_cost_ceiling_myr=2.0, parallel=True
        )

    # No adapter was called for any sibling.
    assert spy.calls == []
    # No ledger row for the refused batch and no spend recorded.
    assert ledger.records_for_run("run_par_refuse") == []
    assert ledger.total_cost("run_par_refuse") == 0.0
    # The refusal names the batch and the ceiling, not a single node.
    msg = str(excinfo.value)
    assert "2.0" in msg
    for nid in SIBLINGS:
        assert nid in msg


@pytest.mark.asyncio
async def test_parallel_batch_completes_at_exact_ceiling():
    """Ceiling 3.0: the batch fits exactly, all three run, ledger total is 3.0."""
    spy, router, ledger = _setup()
    ctx = ExecutionContext("run_par_fit")

    res = await run_dag(
        _three_sibling_dag(), ctx, router, ledger, workflow_cost_ceiling_myr=3.0, parallel=True
    )

    for nid in SIBLINGS:
        assert nid in res.outputs
        assert res.outputs[nid].output["content"] == "ok"
        assert res.outputs[nid].cost_myr == pytest.approx(PER_NODE_MYR)
    assert "emit" in res.outputs
    assert len(spy.calls) == 3
    rows = [r for r in ledger.records_for_run("run_par_fit") if r.node_id in SIBLINGS]
    assert sorted(r.node_id for r in rows) == sorted(SIBLINGS)
    assert all(r.status == "ok" and r.cost_myr == pytest.approx(PER_NODE_MYR) for r in rows)
    assert ledger.total_cost("run_par_fit") == pytest.approx(3.0)


@pytest.mark.asyncio
async def test_sequential_keeps_per_node_gate_behaviour():
    """parallel=False, ceiling 2.0: today's behaviour, unchanged by GH-03.

    The per-layer gate passes every sibling on its own (0 + 1.0 <= 2.0), s1 and
    s2 spend 1.0 each, and s3 is refused by the per-call check inside
    ``invoke_routed_completion`` (2.0 + 1.0 > 2.0), which raises
    ``CostCeilingExceeded`` before the adapter call and writes no row for s3.
    """
    spy, router, ledger = _setup()
    ctx = ExecutionContext("run_seq")

    with pytest.raises(CostCeilingExceeded):
        await run_dag(
            _three_sibling_dag(), ctx, router, ledger, workflow_cost_ceiling_myr=2.0, parallel=False
        )

    assert len(spy.calls) == 2
    rows = ledger.records_for_run("run_seq")
    assert [r.node_id for r in rows] == ["s1", "s2"]
    assert ledger.total_cost("run_seq") == pytest.approx(2.0)


@pytest.mark.asyncio
async def test_max_parallel_batches_are_gated_with_running_total():
    """max_parallel=2, ceiling 2.0: batch [s1, s2] fits and spends 2.0;
    batch [s3] is gated against the updated total and refused with no spend."""
    spy, router, ledger = _setup()
    ctx = ExecutionContext("run_par_batches")

    with pytest.raises(WorkflowCostCeilingExceeded) as excinfo:
        await run_dag(
            _three_sibling_dag(),
            ctx,
            router,
            ledger,
            workflow_cost_ceiling_myr=2.0,
            parallel=True,
            max_parallel=2,
        )

    assert sorted(spy.calls) == ["sibling s1", "sibling s2"]
    rows = ledger.records_for_run("run_par_batches")
    assert sorted(r.node_id for r in rows) == ["s1", "s2"]
    assert ledger.total_cost("run_par_batches") == pytest.approx(2.0)
    assert "s3" in str(excinfo.value)


@pytest.mark.asyncio
async def test_no_ceiling_parallel_is_ungated():
    """No ceiling: the cumulative gate is not consulted and all siblings run."""
    spy, router, ledger = _setup()
    ctx = ExecutionContext("run_par_none")

    res = await run_dag(
        _three_sibling_dag(), ctx, router, ledger, workflow_cost_ceiling_myr=None, parallel=True
    )

    assert all(nid in res.outputs for nid in SIBLINGS)
    assert len(spy.calls) == 3
