"""#241: person data must not reach a model adapter through invoke_routed_completion.

Every assertion is on what the adapter (the process boundary) actually receives,
captured by a recording fake, and on the DAG node output the caller gets back.
All values below are synthetic.
"""

import json

import pytest
from netie.execution.dag_runner import ExecutionContext, run_dag
from netie.execution.executor import invoke_routed_completion
from netie.execution.model_router import BIG_API_PLACEHOLDER, ModelRequest, ModelRouter
from netie.fabrication.dsl_parser import parse_dsl
from netie.result import Ok
from netie.routing.adapters.base import AdapterRequest, AdapterResponse, LLMAdapter
from netie.routing.cost_ledger import CostLedger
from netie.routing.tiers import Tier

NRIC = "S1234567D"
MYKAD = "900101-14-5678"
EMAIL = "jane@example.com"
CARD = "4111 1111 1111 1111"  # public test PAN, Luhn valid
RAW_VALUES = (NRIC, MYKAD, EMAIL, CARD)


class RecordingAdapter(LLMAdapter):
    """Records every request that reaches the process boundary and echoes the prompt."""

    def __init__(self) -> None:
        self.requests: list[AdapterRequest] = []

    async def complete(self, req: AdapterRequest) -> AdapterResponse:
        self.requests.append(req)
        return AdapterResponse(
            content=f"echo: {req.prompt}", prompt_tokens=10, completion_tokens=5, latency_ms=1, raw={}
        )

    def cost_myr(self, prompt_tokens: int, completion_tokens: int) -> float:
        return round(prompt_tokens * 0.001 + completion_tokens * 0.002, 6)


def _router(adapter: LLMAdapter) -> ModelRouter:
    return ModelRouter(
        adapter_registry={"anthropic": adapter, "openai": adapter, "self_hosted": adapter},
        provider_aliases={BIG_API_PLACEHOLDER: "anthropic"},
    )


def _model_req(prompt: str) -> ModelRequest:
    return ModelRequest(
        request_type="judge",
        prompt=prompt,
        default_tier=Tier.T3,
        max_tier=Tier.T3,
        cost_ceiling_myr=10.0,
        provider=BIG_API_PLACEHOLDER,
    )


async def _call(adapter: RecordingAdapter, *, system: str, prompt: str, ledger: CostLedger | None = None):
    return await invoke_routed_completion(
        _router(adapter),
        ledger or CostLedger(),
        run_id="r1",
        workflow_cost_ceiling_myr=10.0,
        node_id="n1",
        model_req=_model_req(prompt),
        adapter_req=AdapterRequest(model="", system=system, prompt=prompt, max_tokens=50),
    )


def _leaks(text: str) -> list[str]:
    return [v for v in RAW_VALUES if v in text]


@pytest.mark.asyncio
async def test_dag_llm_judged_node_never_sends_person_data():
    """The run_dag path from the issue: context carries person data, adapter must not see it."""
    dag_json = {
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
                "system": f"Reviewer. Applicant {NRIC} must not be disclosed.",
                "prompt": f"Check {NRIC}, {MYKAD}, {EMAIL}, card {CARD} and qty 12.",
                "max_tokens": 50,
                "cost_ceiling_myr": 10.0,
                "inputs": [],
            },
            {"id": "e1", "kind": "EMIT", "tier": 0, "inputs": ["j1"]},
        ],
    }
    parsed = parse_dsl(json.dumps(dag_json), "test")
    assert isinstance(parsed, Ok)
    adapter = RecordingAdapter()
    res = await run_dag(
        parsed.value, ExecutionContext("run_pii"), _router(adapter), CostLedger(), workflow_cost_ceiling_myr=None
    )

    assert len(adapter.requests) == 1
    sent = adapter.requests[0]
    assert _leaks(sent.prompt) == []
    assert _leaks(sent.system) == []
    assert "[REDACTED:" in sent.prompt
    assert "qty 12" in sent.prompt
    assert _leaks(json.dumps(res.outputs["j1"], default=str)) == []


@pytest.mark.asyncio
async def test_system_text_alone_is_redacted():
    adapter = RecordingAdapter()
    await _call(adapter, system=f"Customer on file: {EMAIL} / {NRIC}", prompt="Summarise the file.")
    assert _leaks(adapter.requests[0].system) == []
    assert adapter.requests[0].prompt == "Summarise the file."
