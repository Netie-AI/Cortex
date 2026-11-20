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


# --- #241 fix: port, fail-closed call, measured figures, look-alikes ----------

FIGURES = "Revenue RM 1,234,567.89, 1250000.50 kg, 12.345.678 units, range 1200-3400, qty 12"


@pytest.fixture(autouse=True)
def _restore_redactor():
    from CortexOS.security import redact_port

    saved = (redact_port._redactor, redact_port._pack_loaded)
    yield
    redact_port._redactor, redact_port._pack_loaded = saved


def _engine_default_only(monkeypatch):
    from CortexOS.security import redact_port

    redact_port.clear_redactor()
    monkeypatch.setattr(redact_port, "_load_active_pack", lambda: None)


@pytest.mark.asyncio
async def test_default_redactor_masks_when_no_pack_registered(monkeypatch):
    from CortexOS.security import redact_port

    _engine_default_only(monkeypatch)
    adapter = RecordingAdapter()
    await _call(adapter, system=f"Applicant {MYKAD}", prompt=f"{NRIC} {EMAIL} {CARD} {FIGURES}")
    sent = adapter.requests[0]
    assert redact_port.active_redactor() is redact_port.default_redact
    assert _leaks(sent.system + sent.prompt) == []
    assert "[REDACTED:mykad]" in sent.system and "[REDACTED:nric]" in sent.prompt
    assert FIGURES in sent.prompt  # every digit of every figure unchanged


@pytest.mark.asyncio
async def test_dms_pack_registers_its_masker_and_it_is_used():
    from CortexOS.security import redact_port
    from packs.dms.security import pii

    redact_port.clear_redactor()
    pii.redact_port.register_redactor(pii.redact_for_model)
    adapter = RecordingAdapter()
    await _call(adapter, system="Reach the buyer on +60 12-345 6789", prompt=f"{EMAIL} {FIGURES}")
    sent = adapter.requests[0]
    assert redact_port.active_redactor() is pii.redact_for_model
    assert "+60 12-345 6789" not in sent.system and "[REDACTED:phone]" in sent.system
    assert EMAIL not in sent.prompt and FIGURES in sent.prompt


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "disguised",
    [
        "Ｓ１２３４５６７Ｄ",  # fullwidth S1234567D
        "S123​4567D",  # zero-width space inside
        "900101​-14-5678",
    ],
    ids=["fullwidth", "zero_width_nric", "zero_width_mykad"],
)
async def test_look_alike_and_zero_width_forms_are_masked(monkeypatch, disguised):
    _engine_default_only(monkeypatch)
    adapter = RecordingAdapter()
    await _call(adapter, system="", prompt=f"id {disguised} qty 12")
    sent = adapter.requests[0].prompt
    assert disguised not in sent
    assert sent.startswith("id [REDACTED:") and sent.endswith(" qty 12")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("redactor", "reason"),
    [
        (lambda _t: (_ for _ in ()).throw(ValueError(f"boom {NRIC}")), "redactor_raised"),
        (lambda _t: None, "redactor_returned_nothing"),
        (lambda _t: "", "redactor_returned_nothing"),
    ],
    ids=["raises", "returns_none", "returns_empty"],
)
async def test_broken_redactor_refuses_with_zero_adapter_calls_and_an_error_row(redactor, reason):
    from CortexOS.security import redact_port

    redact_port.register_redactor(redactor)
    adapter = RecordingAdapter()
    ledger = CostLedger()
    with pytest.raises(redact_port.RedactionRefused) as exc:
        await _call(adapter, system="s", prompt=f"Check {NRIC}", ledger=ledger)
    assert exc.value.reason == reason
    assert NRIC not in str(exc.value)
    assert adapter.requests == []
    rows = ledger.records_for_run("r1")
    assert [(r.status, r.error) for r in rows] == [("error", reason)]


@pytest.mark.asyncio
async def test_broken_redactor_stops_a_dag_run_before_the_adapter():
    from CortexOS.security import redact_port

    def _raise(_text):
        raise RuntimeError("masker down")

    redact_port.register_redactor(_raise)
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
                "prompt": f"Check {NRIC}",
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
    with pytest.raises(Exception):  # noqa: B017 - the run must not complete
        await run_dag(
            parsed.value, ExecutionContext("run_pii_x"), _router(adapter), CostLedger(), workflow_cost_ceiling_myr=None
        )
    assert adapter.requests == []


def test_redact_port_imports_no_pack():
    import ast
    from pathlib import Path

    from CortexOS.security import redact_port

    tree = ast.parse(Path(redact_port.__file__).read_text(encoding="utf-8"))
    names = [
        n.module if isinstance(n, ast.ImportFrom) else a.name
        for n in ast.walk(tree)
        if isinstance(n, (ast.Import, ast.ImportFrom))
        for a in (n.names if isinstance(n, ast.Import) else [n])
    ]
    assert not [m for m in names if m and (m == "packs" or m.startswith("packs."))]
