"""RAG-03 — doc-RAG only after L0/L1/skill/L2 abstain, never ahead of metrics."""

from __future__ import annotations

from typing import Any

import pytest

from CortexOS.dms.answer_engine import answer
from CortexOS.dms.document_retrieval_port import (
    clear_document_retrieval,
    register_document_retrieval,
)


class _StubProvider:
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
        del question, top_k, source_ids, depth
        return [c for c in self._chunks if str(c.get("space_id")) == str(space_id)]


SPACE = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"


@pytest.fixture(autouse=True)
def _clean_registry():
    clear_document_retrieval()
    yield
    clear_document_retrieval()


@pytest.fixture
def ensure_db():
    from bench.accuracy import _ensure_db_loaded
    from packs.dms.semantic.loader import reload

    _ensure_db_loaded()
    reload()


def test_route_to_metric_beats_document_keywords_without_db():
    """L1 must win over document keywords — no warehouse required."""
    from CortexOS.dms.answer_engine import route_to_metric

    plan = route_to_metric("which suppliers have a risk score above 0.7?")
    assert plan is not None
    assert plan.metric_id == "suppliers_by_risk"


def test_metric_question_wins_over_document_keywords(ensure_db):
    """Governed metric path must not be preempted by a configured retrieval port."""
    register_document_retrieval(
        _StubProvider(
            [
                {
                    "id": "1",
                    "space_id": SPACE,
                    "source_id": "contract.txt",
                    "content": "supplier risk score policy irrelevant",
                }
            ]
        )
    )
    r = answer(
        "which suppliers have a risk score above 0.7?",
        space_id=SPACE,
    )
    assert r["route"] == "sql"
    assert r["layer"] in ("certified", "governed_metric", "query_skill", "generated")
    assert r["badge"] != "document"


def test_certified_metric_not_preempted_by_retrieval(ensure_db):
    r = answer("How many SKUs do we have in inventory?", space_id=SPACE)
    assert r["route"] == "sql"
    assert r["layer"] == "certified"


def test_abstain_falls_through_to_document_rag_with_space(monkeypatch):
    """With L2 off, unmatched doc ask + space_id falls through to retrieval."""
    monkeypatch.setenv("DMS_L2_ENABLED", "0")
    register_document_retrieval(
        _StubProvider(
            [
                {
                    "id": "c1",
                    "space_id": SPACE,
                    "source_id": "sop-cold-chain.txt",
                    "content": "Cold chain SOP requires Bay-3 temperature logs every 4 hours.",
                }
            ]
        )
    )
    r = answer(
        "what does the SOP-DOC-ZZ9 document say about cold-chain bay logs?",
        space_id=SPACE,
    )
    assert r["route"] == "rag"
    assert r["badge"] == "document"
    assert r["layer"] == "rag"
    assert r["sql_used"] is None
    assert r.get("sources")


def test_abstain_without_space_stays_abstain(monkeypatch):
    """Doc-RAG must not fire without space_id; with L2 off, unmatched doc ask abstains."""
    monkeypatch.setenv("DMS_L2_ENABLED", "0")
    register_document_retrieval(
        _StubProvider(
            [
                {
                    "id": "c1",
                    "space_id": SPACE,
                    "source_id": "sop.txt",
                    "content": "Cold chain policy text.",
                }
            ]
        )
    )
    r = answer("what does the SOP-DOC-ZZ9 document say about cold-chain bay logs?")
    assert r["route"] == "needs_clarification"
    assert r["layer"] == "abstain"
    assert r["badge"] == "abstain"


def test_blocked_ddl_still_refused_with_retrieval_configured():
    register_document_retrieval(_StubProvider([]))
    r = answer("Drop table inventory", space_id=SPACE)
    assert r["route"] == "blocked"
    assert r["violations_blocked"] == ["DDL_ATTEMPT"]
