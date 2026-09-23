"""KEV-LOG (#246): the tier-decision log is an artifact an operator reads back.

Every test reads the JSONL file the engine wrote (or asserts it is absent) and
the node result the caller received. The log must never carry prompt text,
never raise into the node path, and be silent under ``CORTEX_DECISION_LOG=0``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from netie.decision import decision_log
from netie.execution.dag_runner import ExecutionContext, run_dag
from netie.execution.errors import CostCeilingExceeded
from netie.execution.executor import invoke_routed_completion
from netie.execution.model_router import BIG_API_PLACEHOLDER, ModelRequest, ModelRouter
from netie.fabrication.dsl_parser import parse_dsl
from netie.result import Ok
from netie.routing.adapters.base import AdapterRequest, AdapterResponse, LLMAdapter
from netie.routing.cost_ledger import CostLedger
from netie.routing.judgment_model import JudgmentModel
from netie.routing.tiers import Tier

SECRET = "PLANTED-SECRET-7f3a9c-do-not-log"
SYSTEM_SECRET = "SYSTEM-SECRET-b81e42"


class SpyAdapter(LLMAdapter):
    def __init__(self, *, fail: Exception | None = None) -> None:
        self.requests: list[AdapterRequest] = []
        self.fail = fail

    async def complete(self, req: AdapterRequest) -> AdapterResponse:
        self.requests.append(req)
        if self.fail is not None:
            raise self.fail
        return AdapterResponse(
            content=f"echo: {req.prompt}",
            prompt_tokens=10,
            completion_tokens=5,
            latency_ms=1,
            raw={},
        )

    def cost_myr(self, prompt_tokens: int, completion_tokens: int) -> float:
        return round(prompt_tokens * 0.001 + completion_tokens * 0.002, 6)


def _router(spy: SpyAdapter, judgment_model: JudgmentModel | None = None) -> ModelRouter:
    return ModelRouter(
        adapter_registry={"anthropic": spy, "openai": spy, "self_hosted": spy},
        provider_aliases={BIG_API_PLACEHOLDER: "anthropic"},
        judgment_model=judgment_model,
    )


def _dag(*, request_type: str, default_tier: str, max_tier: str, prompt: str, system: str):
    r = parse_dsl(
        json.dumps(
            {
                "version": "2.0",
                "intent_hash": "h",
                "entry_node_id": "j1",
                "output_node_id": "e1",
                "nodes": [
                    {
                        "id": "j1",
                        "kind": "llm_judged",
                        "request_type": request_type,
                        "default_tier": default_tier,
                        "max_tier": max_tier,
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
        ),
        "test",
    )
    assert isinstance(r, Ok)
    return r.value


@pytest.fixture
def log_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "engine" / "tier_decisions.jsonl"
    monkeypatch.setenv(decision_log.PATH_ENV, str(path))
    monkeypatch.delenv(decision_log.ENABLE_ENV, raising=False)
    decision_log.reset_write_failures()
    yield path
    decision_log.reset_write_failures()


def _lines(path: Path) -> list[dict]:
    assert path.exists(), f"decision log not written at {path}"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


# ---------------------------------------------------------------------------
# ok path through a real run_dag
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_dag_ok_path_appends_one_decision_line(log_file: Path):
    spy = SpyAdapter()
    ledger = CostLedger()
    ctx = ExecutionContext("run_ok", seed={"customer": "qty 12 of SKU-BETA"})
    dag = _dag(
        request_type="plain_review",
        default_tier="T3",
        max_tier="T3",
        prompt="Review: {customer}",
        system="You are a reviewer.",
    )

    res = await run_dag(dag, ctx, _router(spy), ledger, workflow_cost_ceiling_myr=None)
    assert res.outputs["j1"].output["content"] == "echo: Review: qty 12 of SKU-BETA"

    rows = _lines(log_file)
    assert len(rows) == 1
    row = rows[0]
    assert row["schema_version"] == decision_log.SCHEMA_VERSION
    assert row["run_id"] == "run_ok"
    assert row["node_id"] == "j1"
    assert row["request_type"] == "plain_review"
    assert row["default_tier"] == "T3"
    assert row["max_tier"] == "T3"
    assert row["tier"] == "T3"
    assert row["tier"] == res.outputs["j1"].tier
    assert row["status"] == "ok"
    assert row["error_class"] is None
    assert row["reason"] == "heuristic routing fallback"
    assert row["backend"] == "rules-v0"
    assert row["confidence"] == pytest.approx(0.7)
    assert set(row["probabilities"]) == {"T0", "T1", "T2", "T3"}
    assert row["probabilities"]["T1"] == pytest.approx(0.7)
    assert sum(row["probabilities"].values()) == pytest.approx(1.0)
    assert row["cost_myr"] == pytest.approx(0.02)
    assert row["cost_myr"] == pytest.approx(ledger.records_for_run("run_ok")[0].cost_myr)
    assert isinstance(row["state_hash"], str) and len(row["state_hash"]) == 64
    assert row["ts"].endswith("+00:00")
    assert row["provider"] == "anthropic"
    assert decision_log.write_failures() == 0


# ---------------------------------------------------------------------------
# error path: the adapter raises
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_dag_adapter_error_logs_status_error_with_class(log_file: Path):
    spy = SpyAdapter(fail=TimeoutError("upstream timed out"))
    ledger = CostLedger()
    ctx = ExecutionContext("run_err", seed={})
    dag = _dag(
        request_type="plain_review", default_tier="T3", max_tier="T3", prompt="Review now", system=""
    )

    with pytest.raises(TimeoutError):
        await run_dag(dag, ctx, _router(spy), ledger, workflow_cost_ceiling_myr=None)

    rows = _lines(log_file)
    assert len(rows) == 1
    assert rows[0]["status"] == "error"
    assert rows[0]["error_class"] == "TimeoutError"
    assert rows[0]["tier"] == "T3"
    assert rows[0]["cost_myr"] == 0.0
    assert ledger.records_for_run("run_err")[0].status == "error"


@pytest.mark.asyncio
async def test_cost_ceiling_refusal_logs_error_class(log_file: Path):
    spy = SpyAdapter()
    ledger = CostLedger()
    with pytest.raises(CostCeilingExceeded):
        await invoke_routed_completion(
            _router(spy),
            ledger,
            run_id="run_ceiling",
            workflow_cost_ceiling_myr=0.0,
            node_id="n1",
            model_req=ModelRequest(
                request_type="plain",
                prompt="hello",
                default_tier=Tier.T3,
                max_tier=Tier.T3,
                cost_ceiling_myr=0.0,
                provider=BIG_API_PLACEHOLDER,
            ),
            adapter_req=AdapterRequest(model="", system="", prompt="hello", max_tokens=50),
        )
    assert spy.requests == []
    rows = _lines(log_file)
    assert [r["status"] for r in rows] == ["error"]
    assert rows[0]["error_class"] == "CostCeilingExceeded"
    assert rows[0]["node_id"] == "n1"


# ---------------------------------------------------------------------------
# T0 path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_dag_t0_path_logs(log_file: Path):
    spy = SpyAdapter()
    ledger = CostLedger()
    ctx = ExecutionContext("run_t0", seed={})
    dag = _dag(
        request_type="embedding", default_tier="T0", max_tier="T1", prompt="embed me", system=""
    )

    res = await run_dag(dag, ctx, _router(spy), ledger, workflow_cost_ceiling_myr=None)
    assert res.outputs["j1"].tier == "T0"
    assert spy.requests == []

    rows = _lines(log_file)
    assert len(rows) == 1
    assert rows[0]["tier"] == "T0"
    assert rows[0]["status"] == "ok"
    assert rows[0]["reason"] == "deterministic low-tier task"
    assert rows[0]["confidence"] == pytest.approx(0.99)
    assert rows[0]["cost_myr"] == 0.0
    assert rows[0]["request_type"] == "embedding"


# ---------------------------------------------------------------------------
# no prompt text, ever
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_planted_secret_never_reaches_the_log_file(log_file: Path):
    spy = SpyAdapter()
    ledger = CostLedger()
    ctx = ExecutionContext("run_secret", seed={"customer": SECRET})
    dag = _dag(
        request_type="plain_review",
        default_tier="T3",
        max_tier="T3",
        prompt="Review: {customer}",
        system=f"Operator token {SYSTEM_SECRET}",
    )
    await run_dag(dag, ctx, _router(spy), ledger, workflow_cost_ceiling_myr=None)
    # The adapter did see the prompt: the secret was live in the process.
    assert SECRET in spy.requests[0].prompt

    text = log_file.read_text(encoding="utf-8")
    assert SECRET not in text
    assert SYSTEM_SECRET not in text
    assert "Review:" not in text
    row = _lines(log_file)[0]
    for forbidden in ("content", "prompt", "system"):
        assert forbidden not in row
    # state_hash covers the state dict including content, so it is prompt-specific
    # while being non-invertible: same request without the secret hashes differently.
    assert row["state_hash"] != decision_log.state_hash(
        {
            "request_type": "plain_review",
            "content": "Review: other",
            "context_size": 13,
            "prior_tier_failures": 0,
            "user_tier_budget": "T3",
            "is_vip": False,
        }
    )


def test_state_hash_is_sha256_of_canonical_state():
    state = {"request_type": "x", "content": "hello", "context_size": 5, "is_vip": False}
    h = decision_log.state_hash(state)
    assert len(h) == 64
    assert h == decision_log.state_hash(dict(reversed(list(state.items()))))
    assert h != decision_log.state_hash({**state, "content": "hellO"})


# ---------------------------------------------------------------------------
# never breaks the run; failures are counted
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unwritable_path_does_not_break_the_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    blocker = tmp_path / "not_a_dir"
    blocker.write_text("i am a file, not a directory")
    monkeypatch.setenv(decision_log.PATH_ENV, str(blocker / "tier_decisions.jsonl"))
    monkeypatch.delenv(decision_log.ENABLE_ENV, raising=False)
    decision_log.reset_write_failures()

    spy = SpyAdapter()
    ledger = CostLedger()
    ctx = ExecutionContext("run_unwritable", seed={})
    dag = _dag(request_type="plain_review", default_tier="T3", max_tier="T3", prompt="hi", system="")
    res = await run_dag(dag, ctx, _router(spy), ledger, workflow_cost_ceiling_myr=None)

    assert res.outputs["j1"].output["content"] == "echo: hi"
    assert ledger.records_for_run("run_unwritable")[0].status == "ok"
    assert decision_log.write_failures() == 1
    assert decision_log.last_write_error() is not None
    assert not (blocker / "tier_decisions.jsonl").exists()
    decision_log.reset_write_failures()


@pytest.mark.asyncio
async def test_env_zero_writes_nothing(log_file: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv(decision_log.ENABLE_ENV, "0")
    spy = SpyAdapter()
    ledger = CostLedger()
    ctx = ExecutionContext("run_off", seed={})
    dag = _dag(request_type="plain_review", default_tier="T3", max_tier="T3", prompt="hi", system="")
    res = await run_dag(dag, ctx, _router(spy), ledger, workflow_cost_ceiling_myr=None)
    assert res.outputs["j1"].output["content"] == "echo: hi"
    assert not log_file.exists()
    assert decision_log.write_failures() == 0


# ---------------------------------------------------------------------------
# a live decision backend is named but never re-asked
# ---------------------------------------------------------------------------


class _CountingBackend:
    name = "counting"

    def __init__(self) -> None:
        self.calls = 0

    def evaluate(self, question, state):
        from netie.decision import RawDecision

        self.calls += 1
        scores = tuple(0.97 if label == "T2" else 0.01 for label in question.labels)
        return RawDecision(scores=scores, is_logits=False, calibrated=True, backend=self.name)


@pytest.mark.asyncio
async def test_live_backend_is_named_without_a_second_call(log_file: Path):
    backend = _CountingBackend()
    jm = JudgmentModel(decision_backend=backend, check_order=False)
    spy = SpyAdapter()
    ledger = CostLedger()
    ctx = ExecutionContext("run_backend", seed={})
    dag = _dag(request_type="plain_review", default_tier="T1", max_tier="T3", prompt="hi", system="")
    res = await run_dag(dag, ctx, _router(spy, jm), ledger, workflow_cost_ceiling_myr=None)

    assert res.outputs["j1"].tier == "T2"
    assert backend.calls == 1, "the log must not double backend traffic"
    row = _lines(log_file)[0]
    assert row["backend"] == "counting"
    assert row["tier"] == "T2"
    assert row["confidence"] is None
    assert row["probabilities"] is None
    assert row["reason"].startswith("counting choice")


def test_default_path_is_gitignored_engine_runtime_state(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv(decision_log.PATH_ENV, raising=False)
    from netie.paths import data_path

    assert decision_log.log_path() == data_path("engine", "tier_decisions.jsonl")
