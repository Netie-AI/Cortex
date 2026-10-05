"""Tests must never add synthetic decisions to the runtime calibration logs."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from CortexOS.execution.dag_runner import ExecutionContext, run_dag
from CortexOS.execution.model_router import BIG_API_PLACEHOLDER, ModelRouter
from CortexOS.fabrication.dsl_parser import parse_dsl
from CortexOS.result import Ok
from CortexOS.routing.adapters.base import AdapterRequest, AdapterResponse, LLMAdapter
from CortexOS.routing.cost_ledger import CostLedger

_RUNTIME_LOG_FILES = {
    name: Path(__file__).resolve().parents[2] / "data" / "engine" / name
    for name in ("tier_decisions.jsonl", "tier_shadow.jsonl")
}


def _runtime_log_state() -> dict[str, tuple[int, int] | None]:
    state: dict[str, tuple[int, int] | None] = {}
    for name, path in _RUNTIME_LOG_FILES.items():
        try:
            stat = path.stat()
        except FileNotFoundError:
            state[name] = None
        else:
            state[name] = (stat.st_size, stat.st_mtime_ns)
    return state


class _EchoAdapter(LLMAdapter):
    async def complete(self, req: AdapterRequest) -> AdapterResponse:
        return AdapterResponse(
            content=f"echo: {req.prompt}",
            prompt_tokens=1,
            completion_tokens=1,
            latency_ms=1,
            raw={},
        )

    def cost_myr(self, prompt_tokens: int, completion_tokens: int) -> float:
        return 0.0


def _routed_dag():
    parsed = parse_dsl(
        json.dumps(
            {
                "version": "2.0",
                "intent_hash": "test-runtime-log-isolation",
                "entry_node_id": "route",
                "output_node_id": "emit",
                "nodes": [
                    {
                        "id": "route",
                        "kind": "llm_judged",
                        "request_type": "plain_review",
                        "default_tier": "T3",
                        "max_tier": "T3",
                        "provider": BIG_API_PLACEHOLDER,
                        "prompt": "Review this test decision.",
                        "max_tokens": 10,
                        "cost_ceiling_myr": 1.0,
                        "inputs": [],
                    },
                    {"id": "emit", "kind": "EMIT", "tier": 0, "inputs": ["route"]},
                ],
            }
        ),
        "test",
    )
    assert isinstance(parsed, Ok)
    return parsed.value


@pytest.mark.asyncio
async def test_routed_dag_leaves_runtime_logs_untouched(
    runtime_log_snapshot,
    isolate_runtime_logs,
    monkeypatch,
    tmp_path,
):
    monkeypatch.setenv("CORTEX_STEP_JOURNAL", "0")
    before = _runtime_log_state()
    assert before == runtime_log_snapshot
    assert all(path.parent == tmp_path for path in isolate_runtime_logs.values())

    adapter = _EchoAdapter()
    router = ModelRouter(
        adapter_registry={
            "anthropic": adapter,
            "openai": adapter,
            "self_hosted": adapter,
        },
        provider_aliases={BIG_API_PLACEHOLDER: "anthropic"},
    )
    result = await run_dag(
        _routed_dag(),
        ExecutionContext("runtime-log-isolation"),
        router,
        CostLedger(),
        workflow_cost_ceiling_myr=None,
    )

    assert result.outputs["route"].output["content"] == "echo: Review this test decision."
    assert _runtime_log_state() == before


@pytest.fixture
def explicit_runtime_log_paths(isolate_runtime_logs, monkeypatch, tmp_path):
    paths = {
        "CORTEX_DECISION_LOG_PATH": tmp_path / "explicit-decisions.jsonl",
        "CORTEX_KEV_SHADOW_PATH": tmp_path / "explicit-shadow.jsonl",
    }
    for env_name, path in paths.items():
        monkeypatch.setenv(env_name, os.fspath(path))
    return paths


def test_explicit_per_test_paths_are_not_overridden(explicit_runtime_log_paths):
    assert {
        env_name: Path(os.environ[env_name])
        for env_name in explicit_runtime_log_paths
    } == explicit_runtime_log_paths
