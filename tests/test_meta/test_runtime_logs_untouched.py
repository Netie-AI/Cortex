"""H2-TEST-ISOLATION (#254): the suite never writes the runtime decision or shadow logs.

``data/engine/tier_decisions.jsonl`` and ``data/engine/tier_shadow.jsonl`` are
the files KEV-CALIB reads as real outcomes. Every test here asserts the artifact
an operator would inspect: the size and mtime (or absence) of both real files,
snapshotted at session start by the ``tests/runtime_log_isolation.py`` plugin, and the JSONL rows that
the routed DAG / shadow evaluation actually wrote to the per-test tmp path.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import httpx
import pytest
import respx
from netie.decision import decision_log, shadow
from netie.execution.dag_runner import ExecutionContext, run_dag
from netie.execution.model_router import BIG_API_PLACEHOLDER, ModelRouter
from netie.fabrication.dsl_parser import parse_dsl
from netie.paths import data_path
from netie.result import Ok
from netie.routing.adapters.base import AdapterRequest, AdapterResponse, LLMAdapter
from netie.routing.cost_ledger import CostLedger
from netie.routing.judgment_model import JudgmentModel, JudgmentRequest
from netie.routing.tiers import Tier

KEV = "http://127.0.0.1:8787"

# The real runtime files, resolved as the engine resolves them and independent
# of the isolation plugin, so the DAG / shadow tests below fail on base for the
# real reason (a row landed in data/engine) and not for a missing fixture.
REAL_FILES = {
    name: data_path("engine", name) for name in (decision_log.DEFAULT_FILENAME, shadow.DEFAULT_FILENAME)
}


def _snapshot() -> dict[str, tuple[int, int] | None]:
    out: dict[str, tuple[int, int] | None] = {}
    for name, path in REAL_FILES.items():
        out[name] = (path.stat().st_size, path.stat().st_mtime_ns) if path.exists() else None
    return out


class EchoAdapter(LLMAdapter):
    async def complete(self, req: AdapterRequest) -> AdapterResponse:
        return AdapterResponse(content=f"echo: {req.prompt}", prompt_tokens=10, completion_tokens=5, latency_ms=1, raw={})

    def cost_myr(self, prompt_tokens: int, completion_tokens: int) -> float:
        return round(prompt_tokens * 0.001 + completion_tokens * 0.002, 6)


def _router(judgment_model: JudgmentModel | None = None) -> ModelRouter:
    spy = EchoAdapter()
    return ModelRouter(
        adapter_registry={"anthropic": spy, "openai": spy, "self_hosted": spy},
        provider_aliases={BIG_API_PLACEHOLDER: "anthropic"},
        judgment_model=judgment_model,
    )


def _dag():
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
                        "request_type": "plain_review",
                        "default_tier": "T3",
                        "max_tier": "T3",
                        "provider": BIG_API_PLACEHOLDER,
                        "prompt": "Review: {customer}",
                        "system": "You are a reviewer.",
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


def _rows(path: Path) -> list[dict]:
    assert path.exists(), f"expected rows at {path}"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def _kev_answer(request: httpx.Request) -> httpx.Response:
    probs = {t.value: (0.91 if t is Tier.T1 else 0.03) for t in Tier}
    return httpx.Response(
        200,
        json={"answers": {"q": {"type": "choice", "choice": "T1", "confidence": 0.9, "probabilities": probs}}},
    )


# ---------------------------------------------------------------------------
# the fixture wiring itself
# ---------------------------------------------------------------------------


def test_fixture_env_names_match_the_engine_modules(runtime_log_guard):
    """The fixture spells the env names rather than importing the engine; they must agree."""
    assert set(runtime_log_guard.env) == {decision_log.PATH_ENV, shadow.PATH_ENV}
    assert runtime_log_guard.env[decision_log.PATH_ENV] == decision_log.DEFAULT_FILENAME
    assert runtime_log_guard.env[shadow.PATH_ENV] == shadow.DEFAULT_FILENAME
    assert runtime_log_guard.files == REAL_FILES
    assert runtime_log_guard.snapshot() == _snapshot()


def test_isolation_plugin_is_registered_for_the_whole_suite(request):
    """The plugin is loaded through ``addopts`` in pyproject.toml, not only when named."""
    assert request.config.pluginmanager.has_plugin("tests.runtime_log_isolation")
    assert "-p tests.runtime_log_isolation" in " ".join(request.config.getini("addopts"))


def test_runtime_files_unchanged_since_session_start(runtime_log_guard):
    """Whatever ran before this test in the session left both real files alone."""
    assert _snapshot() == runtime_log_guard.baseline


def test_engine_resolves_both_logs_to_a_per_test_tmp_path(tmp_path: Path):
    """The path the engine will write is under this test's tmp_path, not data/engine."""
    for resolved in (decision_log.log_path(), shadow.shadow_path()):
        assert resolved.is_relative_to(tmp_path), resolved
        assert resolved not in REAL_FILES.values()
    assert decision_log.log_path() != shadow.shadow_path()


# ---------------------------------------------------------------------------
# a routed DAG and a shadow evaluation in-process leave the real files untouched
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_routed_dag_writes_tmp_log_and_leaves_runtime_files_untouched(tmp_path: Path):
    before = _snapshot()

    res = await run_dag(
        _dag(),
        ExecutionContext("run_meta", seed={"customer": "qty 12 of SKU-BETA"}),
        _router(),
        CostLedger(),
        workflow_cost_ceiling_myr=None,
    )
    assert res.outputs["j1"].output["content"] == "echo: Review: qty 12 of SKU-BETA"

    # the decision was logged, to the isolated path, with the served tier
    rows = _rows(decision_log.log_path())
    assert [r["node_id"] for r in rows] == ["j1"]
    assert rows[0]["run_id"] == "run_meta"
    assert rows[0]["status"] == "ok"
    assert decision_log.write_failures() == 0
    assert decision_log.log_path().is_relative_to(tmp_path)

    assert _snapshot() == before, "a routed DAG wrote the real decision log"


@respx.mock
def test_shadow_evaluation_writes_tmp_log_and_leaves_runtime_files_untouched(monkeypatch, tmp_path: Path):
    before = _snapshot()
    monkeypatch.setenv("CORTEX_KEV_URL", KEV)
    monkeypatch.setenv("CORTEX_KEV_SHADOW", "1")
    respx.post(f"{KEV}/v1/systemone").mock(side_effect=_kev_answer)
    shadow.reset_write_failures()

    jm = JudgmentModel.from_env()
    assert jm.shadow is not None
    served = jm.decide(JudgmentRequest(request_type="chat", content="hello there"))
    assert served.tier == JudgmentModel().rules_decide(JudgmentRequest(request_type="chat", content="hello there")).tier

    rows = shadow.read_rows(shadow.shadow_path())
    assert len(rows) == 1
    assert rows[0]["rules_tier"] == served.tier.value
    assert rows[0]["kev_choice"] == "T1"
    assert shadow.write_failures() == 0
    assert shadow.shadow_path().is_relative_to(tmp_path)

    assert _snapshot() == before, "a shadow evaluation wrote the real shadow log"


# ---------------------------------------------------------------------------
# no false positive: a test that sets its own path keeps it
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_explicit_per_test_path_is_not_overridden(monkeypatch, tmp_path: Path):
    mine = tmp_path / "mine" / "decisions.jsonl"
    monkeypatch.setenv(decision_log.PATH_ENV, str(mine))
    my_shadow = tmp_path / "mine" / "shadow.jsonl"
    monkeypatch.setenv(shadow.PATH_ENV, str(my_shadow))

    assert decision_log.log_path() == mine
    assert shadow.shadow_path() == my_shadow
    assert os.environ[decision_log.PATH_ENV] == str(mine)

    await run_dag(
        _dag(),
        ExecutionContext("run_mine", seed={"customer": "x"}),
        _router(),
        CostLedger(),
        workflow_cost_ceiling_myr=None,
    )
    rows = _rows(mine)
    assert [r["run_id"] for r in rows] == ["run_mine"]
    assert not (tmp_path / "runtime_logs" / decision_log.DEFAULT_FILENAME).exists()


def test_disabled_log_still_writes_nothing(monkeypatch):
    """``CORTEX_DECISION_LOG=0`` is honoured regardless of the isolated path."""
    monkeypatch.setenv(decision_log.ENABLE_ENV, "0")
    assert decision_log.enabled() is False
    assert not decision_log.log_path().exists()


@pytest.fixture
def own_log_paths(monkeypatch, tmp_path: Path) -> tuple[Path, Path]:
    """A test's own (non-autouse) fixture that sets both paths."""
    mine = tmp_path / "fixture_owned" / "decisions.jsonl"
    my_shadow = tmp_path / "fixture_owned" / "shadow.jsonl"
    monkeypatch.setenv(decision_log.PATH_ENV, str(mine))
    monkeypatch.setenv(shadow.PATH_ENV, str(my_shadow))
    return mine, my_shadow


@pytest.mark.asyncio
async def test_path_set_by_a_test_fixture_is_not_overridden(own_log_paths, tmp_path: Path):
    mine, my_shadow = own_log_paths
    assert decision_log.log_path() == mine
    assert shadow.shadow_path() == my_shadow
    await run_dag(
        _dag(),
        ExecutionContext("run_fixture_owned", seed={"customer": "x"}),
        _router(),
        CostLedger(),
        workflow_cost_ceiling_myr=None,
    )
    assert [r["run_id"] for r in _rows(mine)] == ["run_fixture_owned"]
    assert not (tmp_path / "runtime_logs" / decision_log.DEFAULT_FILENAME).exists()
