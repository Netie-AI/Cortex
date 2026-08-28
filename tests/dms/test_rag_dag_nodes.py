"""A3 — rag_* DAG nodes + Modular-RAG templates."""

from __future__ import annotations

import pytest

from CortexOS.execution.dag_runner import ExecutionContext, run_dag
from CortexOS.execution.model_router import ModelRouter
from CortexOS.rag.templates import get_template
from CortexOS.routing.cost_ledger import CostLedger

CORPUS = [
    {"id": "1", "text": "Oracle AI Database stores vectors JSON and graph in one engine."},
    {"id": "2", "text": "Cortex dag_runner executes RAG_RETRIEVE RAG_RERANK RAG_ANSWER nodes."},
    {"id": "3", "text": "Unrelated shipping logistics warehouse pallet counts."},
]


@pytest.mark.asyncio
async def test_template_basic_retrieve_answer():
    dag = get_template("basic")
    ctx = ExecutionContext(
        "t-basic",
        {"query": "dag_runner RAG nodes", "prompt": "dag_runner RAG nodes", "rag_corpus": CORPUS},
    )
    result = await run_dag(dag, ctx, ModelRouter(), CostLedger())
    out = result.outputs["answer"].output
    assert out["n_hits"] >= 1
    assert "citations" in out
    assert out["rounds"] == 1


@pytest.mark.asyncio
async def test_template_high_reranks():
    dag = get_template("high")
    ctx = ExecutionContext(
        "t-high",
        {"query": "vectors JSON graph", "prompt": "vectors JSON graph", "rag_corpus": CORPUS},
    )
    result = await run_dag(dag, ctx, ModelRouter(), CostLedger())
    assert "rerank" in result.outputs
    assert result.outputs["rerank"].output.get("reranked") is True
    assert result.outputs["answer"].output["n_hits"] >= 1


@pytest.mark.asyncio
async def test_template_max_two_rounds():
    dag = get_template("max")
    ctx = ExecutionContext(
        "t-max",
        {"query": "Cortex RAG_ANSWER", "prompt": "Cortex RAG_ANSWER", "rag_corpus": CORPUS},
    )
    result = await run_dag(dag, ctx, ModelRouter(), CostLedger())
    out = result.outputs["answer"].output
    assert out.get("rounds") == 2
    assert "retrieve_r2" in result.outputs


@pytest.mark.asyncio
async def test_rag_compose_run_plan():
    from CortexOS.execution.run_plan import execute_run_plan

    plan = {"runner": "rag_compose", "preset": "rag"}
    body = {
        "query": "Oracle vectors",
        "depth": "basic",
        "rag_corpus": CORPUS,
    }
    out = await execute_run_plan(plan, body)
    assert out["ok"] is True
    assert out["template"] == "modular_hybrid_basic"
    assert out["output"]["n_hits"] >= 1
