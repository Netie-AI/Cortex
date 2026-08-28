from dataclasses import replace

import pytest
from netie.execution.errors import CostCeilingExceeded
from netie.execution.executor import invoke_routed_completion
from netie.execution.model_router import BIG_API_PLACEHOLDER, ModelRequest, ModelRouter
from netie.routing.adapters.base import AdapterRequest, AdapterResponse, LLMAdapter
from netie.routing.cost_ledger import CostLedger, NodeExecutionRecord, now_utc
from netie.routing.tiers import Tier


class StubAdapter(LLMAdapter):
    def __init__(self, projected_rate: float = 0.0) -> None:
        self._projected_rate = projected_rate

    async def complete(self, req) -> AdapterResponse:  # type: ignore[no-untyped-def]
        del req
        return AdapterResponse(content="ok", prompt_tokens=10, completion_tokens=20, latency_ms=1, raw={})

    def cost_myr(self, prompt_tokens: int, completion_tokens: int) -> float:
        if self._projected_rate > 0:
            return round(self._projected_rate, 6)
        return round(prompt_tokens * 0.001 + completion_tokens * 0.002, 6)


def _stub_registry() -> dict[str, LLMAdapter]:
    stub = StubAdapter()
    return {"anthropic": stub, "openai": stub, "self_hosted": stub}


@pytest.mark.asyncio
async def test_ledger_add_updates_run_totals():
    ledger = CostLedger()
    rec = NodeExecutionRecord(
        run_id="run_a",
        node_id="n1",
        tier="T1",
        model="stub",
        latency_ms=12,
        prompt_tokens=10,
        completion_tokens=11,
        cost_myr=0.05,
        cache_hit=False,
        started_at=now_utc(),
        ended_at=now_utc(),
        status="ok",
        ceiling_myr=1.0,
        error=None,
    )
    await ledger.add(rec)
    assert ledger.total_cost("run_a") == pytest.approx(0.05)
    await ledger.add(replace(rec, node_id="n2", cost_myr=0.06))
    assert ledger.total_cost("run_a") == pytest.approx(0.11)


@pytest.mark.asyncio
async def test_enforce_ceiling_with_projection():
    ledger = CostLedger()
    await ledger.add(
        NodeExecutionRecord(
            run_id="r",
            node_id="n1",
            tier="T2",
            model="stub",
            latency_ms=1,
            prompt_tokens=0,
            completion_tokens=0,
            cost_myr=0.45,
            cache_hit=False,
            started_at=now_utc(),
            ended_at=now_utc(),
            status="ok",
            ceiling_myr=10.0,
            error=None,
        )
    )
    assert ledger.enforce_ceiling("r", ceiling_myr=0.5, projected_additional_myr=0.049)
    assert not ledger.enforce_ceiling("r", ceiling_myr=0.5, projected_additional_myr=0.051)


@pytest.mark.asyncio
async def test_executor_blocks_before_adapter_when_projection_exceeds():
    pricey = StubAdapter(projected_rate=10.0)
    reg = {"anthropic": pricey, "openai": pricey, "self_hosted": pricey}
    router = ModelRouter(
        adapter_registry=reg,
        provider_aliases={BIG_API_PLACEHOLDER: "anthropic"},
    )
    ledger = CostLedger()
    req = ModelRequest(
        request_type="eval_judge",
        prompt="Judge this",
        default_tier=Tier.T3,
        max_tier=Tier.T3,
        provider=BIG_API_PLACEHOLDER,
        cost_ceiling_myr=1.0,
    )
    with pytest.raises(CostCeilingExceeded):
        _ = await invoke_routed_completion(
            router,
            ledger,
            run_id="run_x",
            workflow_cost_ceiling_myr=5.0,
            node_id="node_a",
            model_req=req,
            adapter_req=AdapterRequest(model="", system="", prompt="short", max_tokens=50),
            node_cost_ceiling_myr=1.0,
        )


@pytest.mark.asyncio
async def test_executor_persists_ok_record_via_ledger_cache():
    router = ModelRouter(
        adapter_registry=_stub_registry(),
        provider_aliases={BIG_API_PLACEHOLDER: "anthropic"},
    )
    ledger = CostLedger()
    req = ModelRequest(
        request_type="plain",
        prompt="Hello",
        default_tier=Tier.T3,
        max_tier=Tier.T3,
        provider=BIG_API_PLACEHOLDER,
        cost_ceiling_myr=10.0,
    )
    outcome = await invoke_routed_completion(
        router,
        ledger,
        run_id="run_y",
        workflow_cost_ceiling_myr=50.0,
        node_id="node_b",
        model_req=req,
        adapter_req=AdapterRequest(model="", system="", prompt="Ping", max_tokens=100),
    )
    assert outcome.response.content == "ok"
    assert outcome.tier in {"T1", "T2", "T3"}
    assert outcome.cost_myr > 0
    assert len(ledger.records()) == 1
    assert ledger.records()[0].status == "ok"
    assert ledger.records()[0].ceiling_myr == pytest.approx(10.0)
    assert ledger.total_cost("run_y") > 0


@pytest.mark.asyncio
async def test_records_for_run_filters_by_run_id():
    ledger = CostLedger()
    rec_a = NodeExecutionRecord(
        run_id="run_a",
        node_id="n1",
        tier="T1",
        model="stub",
        latency_ms=12,
        prompt_tokens=10,
        completion_tokens=11,
        cost_myr=0.05,
        cache_hit=False,
        started_at=now_utc(),
        ended_at=now_utc(),
        status="ok",
        ceiling_myr=1.0,
        error=None,
    )
    await ledger.add(rec_a)
    await ledger.add(replace(rec_a, run_id="run_b", node_id="n2", cost_myr=0.03))
    await ledger.add(replace(rec_a, node_id="n3", cost_myr=0.02))
    assert len(ledger.records_for_run("run_a")) == 2
    assert len(ledger.records_for_run("run_b")) == 1
    assert ledger.total_cost("run_a") == pytest.approx(0.07)


@pytest.mark.asyncio
async def test_t0_skips_adapter_and_records_zero_cost():
    class FailAdapter(LLMAdapter):
        async def complete(self, req) -> AdapterResponse:  # type: ignore[no-untyped-def]
            raise AssertionError("T0 must not invoke the adapter")

        def cost_myr(self, prompt_tokens: int, completion_tokens: int) -> float:
            return 99.0

    router = ModelRouter(
        adapter_registry={
            "anthropic": FailAdapter(),
            "openai": FailAdapter(),
            "self_hosted": FailAdapter(),
        },
        provider_aliases={BIG_API_PLACEHOLDER: "anthropic"},
    )
    ledger = CostLedger()
    req = ModelRequest(
        request_type="intent_classify",
        prompt="how many items in stock",
        default_tier=Tier.T0,
        max_tier=Tier.T0,
        provider=BIG_API_PLACEHOLDER,
        cost_ceiling_myr=1.0,
    )
    outcome = await invoke_routed_completion(
        router,
        ledger,
        run_id="run_t0",
        workflow_cost_ceiling_myr=5.0,
        node_id="node_t0",
        model_req=req,
        adapter_req=AdapterRequest(model="", system="", prompt="how many items", max_tokens=50),
    )
    assert outcome.tier == "T0"
    assert outcome.cost_myr == 0.0
    assert outcome.response.raw.get("skipped_adapter") is True
    recs = ledger.records_for_run("run_t0")
    assert len(recs) == 1
    assert recs[0].cost_myr == 0.0
    assert recs[0].tier == "T0"
