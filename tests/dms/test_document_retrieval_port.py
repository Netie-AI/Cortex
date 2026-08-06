"""RAG-02 document retrieval runs through an engine-owned port, not CONTRACTS_DIR.

The regression guard is the same inversion as ``sql_generation_port``: the pack
registers into the engine; ``answer_engine`` must not import ``packs.dms.retrieval``.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

import pytest

from CortexOS.dms.document_retrieval_port import (
    DocumentRetrievalNotRegistered,
    DocumentRetrievalProvider,
    clear_document_retrieval,
    register_document_retrieval,
    registered_document_retrieval,
    resolve_document_retrieval,
)

ROOT = Path(__file__).resolve().parents[2]
ANSWER_ENGINE = ROOT / "CortexOS" / "dms" / "answer_engine.py"


class _StubProvider:
    """In-memory corpus for unit tests — simulates DMS-scoped rows."""

    def __init__(self, chunks: list[dict[str, Any]]) -> None:
        self._chunks = chunks

    def is_configured(self) -> bool:
        return True

    def retrieve(
        self,
        question: str,
        *,
        space_id: str,
        top_k: int = 8,
        source_ids: list[str] | None = None,
        depth: str | None = None,
    ) -> list[dict[str, Any]]:
        del depth
        from CortexOS.rag import lexical

        scoped = [c for c in self._chunks if str(c.get("space_id")) == str(space_id)]
        if source_ids:
            allowed = {str(s) for s in source_ids}
            scoped = [c for c in scoped if str(c.get("source_id")) in allowed]
        docs = [
            {
                "id": c.get("id"),
                "text": c.get("content") or "",
                "space_id": c.get("space_id"),
                "source_id": c.get("source_id"),
            }
            for c in scoped
        ]
        ranked = lexical.retrieve(question, docs, top_k=top_k)
        return [
            {
                "id": h.get("id"),
                "space_id": h.get("space_id"),
                "source_id": h.get("source_id"),
                "content": h.get("text") or "",
                "score": h.get("score"),
            }
            for h in ranked
        ]


@pytest.fixture(autouse=True)
def _clean_registry():
    clear_document_retrieval()
    yield
    clear_document_retrieval()


def test_stub_satisfies_the_declared_protocol():
    assert isinstance(_StubProvider([]), DocumentRetrievalProvider)


def test_register_then_resolve_returns_the_same_provider():
    provider = _StubProvider([])
    register_document_retrieval(provider)
    assert registered_document_retrieval() is provider
    assert resolve_document_retrieval() is provider


def test_registered_does_not_trigger_a_pack_import():
    assert registered_document_retrieval() is None


def test_resolve_loads_the_active_pack_which_registers_dms(monkeypatch):
    monkeypatch.setenv("PACK", "dms")
    import netie.config

    netie.config._cached_config = None
    provider = resolve_document_retrieval()
    assert provider.is_configured() in (True, False)
    assert hasattr(provider, "retrieve")


def test_an_inactive_pack_abstains_rather_than_crashing():
    with pytest.raises(DocumentRetrievalNotRegistered):
        resolve_document_retrieval()


def test_stub_returns_only_matching_space_chunks():
    space_a = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    space_b = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
    provider = _StubProvider(
        [
            {
                "id": "1",
                "space_id": space_a,
                "source_id": "s1",
                "content": "Bay-3 leakage notes for Space A",
            },
            {
                "id": "2",
                "space_id": space_b,
                "source_id": "s2",
                "content": "Forklift battery schedule for Space B",
            },
        ]
    )
    register_document_retrieval(provider)
    hits = resolve_document_retrieval().retrieve(
        "Bay-3 leakage",
        space_id=space_a,
        top_k=5,
    )
    assert hits
    assert all(str(h.get("space_id")) == space_a for h in hits)
    assert not any("Forklift" in str(h.get("content") or "") for h in hits)

    empty = resolve_document_retrieval().retrieve("Bay-3", space_id=space_b, top_k=5)
    assert empty == []


def test_answer_engine_holds_no_retrieval_pack_import():
    tree = ast.parse(ANSWER_ENGINE.read_text(encoding="utf-8"))
    offenders: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and (node.module or "").startswith(
            "packs.dms.retrieval"
        ):
            offenders.append(f"line {node.lineno}: from {node.module} import ...")
        if isinstance(node, ast.Import):
            offenders += [
                f"line {node.lineno}: import {a.name}"
                for a in node.names
                if a.name.startswith("packs.dms.retrieval")
            ]
    assert not offenders, (
        "answer_engine must reach document retrieval through "
        "CortexOS.dms.document_retrieval_port:\n" + "\n".join(offenders)
    )
