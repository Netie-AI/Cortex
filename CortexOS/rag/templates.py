"""Modular-RAG DSL templates (Basic / High / Max) for Cortex dag_runner."""

from __future__ import annotations

import json
from typing import Any

from CortexOS.fabrication.dsl_parser import AgenticDSLProgram, DSLNode, NodeType


def template_basic() -> AgenticDSLProgram:
    retrieve = DSLNode(
        id="retrieve",
        kind=NodeType.RAG_RETRIEVE,
        annotations={"top_k": 8, "depth": "basic"},
    )
    answer = DSLNode(id="answer", kind=NodeType.RAG_ANSWER, inputs=["retrieve"])
    return AgenticDSLProgram(
        nodes=[retrieve, answer],
        entry_node_id="retrieve",
        output_node_id="answer",
        intent_hash="modular_hybrid_basic",
    )


def template_high() -> AgenticDSLProgram:
    retrieve = DSLNode(
        id="retrieve",
        kind=NodeType.RAG_RETRIEVE,
        annotations={"top_k": 20, "depth": "high"},
    )
    rerank = DSLNode(
        id="rerank",
        kind=NodeType.RAG_RERANK,
        inputs=["retrieve"],
        annotations={"top_n": 10},
    )
    answer = DSLNode(id="answer", kind=NodeType.RAG_ANSWER, inputs=["rerank"])
    return AgenticDSLProgram(
        nodes=[retrieve, rerank, answer],
        entry_node_id="retrieve",
        output_node_id="answer",
        intent_hash="modular_hybrid_high",
    )


def template_max() -> AgenticDSLProgram:
    """Max: retrieve → rerank → answer, then a corrective second retrieve→answer."""
    retrieve = DSLNode(
        id="retrieve",
        kind=NodeType.RAG_RETRIEVE,
        annotations={"top_k": 40, "depth": "max", "round": 1},
    )
    rerank = DSLNode(
        id="rerank",
        kind=NodeType.RAG_RERANK,
        inputs=["retrieve"],
        annotations={"top_n": 12},
    )
    answer1 = DSLNode(id="answer_r1", kind=NodeType.RAG_ANSWER, inputs=["rerank"])
    retrieve2 = DSLNode(
        id="retrieve_r2",
        kind=NodeType.RAG_RETRIEVE,
        inputs=["answer_r1"],
        annotations={"top_k": 40, "depth": "max", "round": 2, "corrective": True},
    )
    answer2 = DSLNode(id="answer", kind=NodeType.RAG_ANSWER, inputs=["retrieve_r2", "answer_r1"])
    return AgenticDSLProgram(
        nodes=[retrieve, rerank, answer1, retrieve2, answer2],
        entry_node_id="retrieve",
        output_node_id="answer",
        intent_hash="modular_agentic_max",
    )


TEMPLATES: dict[str, Any] = {
    "basic": template_basic,
    "high": template_high,
    "max": template_max,
    "modular_hybrid_basic": template_basic,
    "modular_hybrid_high": template_high,
    "modular_agentic_max": template_max,
}


def get_template(depth_or_id: str) -> AgenticDSLProgram:
    key = (depth_or_id or "high").strip().lower()
    factory = TEMPLATES.get(key) or TEMPLATES["high"]
    return factory()


def template_json(depth_or_id: str) -> dict[str, Any]:
    prog = get_template(depth_or_id)
    return json.loads(prog.model_dump_json(by_alias=True))
