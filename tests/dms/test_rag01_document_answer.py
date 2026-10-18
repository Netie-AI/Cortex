"""RAG-01: a document answer is a cited answer from relevant passages, or nothing.

The stub this replaces returned the first 400 characters of any file sharing one
word with the question. Assertions are on the served envelope of POST
/dms/query (CLAUDE.md s8): badge, rows, sources and the rendered answer text,
plus the passages the model was shown.
"""

from __future__ import annotations

from typing import Any

import pytest

from CortexOS.dms.query_service import CONTRACTS_DIR
from CortexOS.rag import documents
from tests.dms.test_rag03_answer_routing import (  # noqa: F401
    DOC_HIT_Q,
    SESSION,
    _ask_dms,
    _badge,
    _sources,
    dms_http,
)

SPACE = "alpha"
PAYMENT = "Payment terms: Net 30 from invoice date."
DATA_Q = "which shipments were late last week by carrier"


@pytest.fixture(autouse=True)
def _corpus() -> None:
    if not CONTRACTS_DIR.is_dir() or not any(CONTRACTS_DIR.glob("*.txt")):
        pytest.fail(f"RAG-01 needs the sample documents in {CONTRACTS_DIR}")


def _ask(client: Any, question: str = DOC_HIT_Q) -> dict[str, Any]:
    client.bind_session(SESSION, {"transactions": "TRUE"}, space_id=SPACE)
    return _ask_dms(client, question, SESSION, SPACE)


def _assert_document(body: dict[str, Any]) -> str:
    assert _badge(body) == "document", body
    assert body.get("layer") == "rag" and body.get("route") == "rag", body
    assert body.get("rows") in ([], None)
    assert body.get("sql_used") is None
    assert _sources(body) == ["acme_agreement.txt"], body
    text = body.get("answer") or ""
    assert "Sources: [1] acme_agreement.txt" in text, text
    return text


def test_model_answer_is_served_with_its_citation_and_source(armed_openvault, dms_http) -> None:  # noqa: F811
    armed_openvault.reply("Invoices are payable Net 30 from the invoice date [1].")

    text = _assert_document(_ask(dms_http))

    assert text.startswith("Invoices are payable Net 30 from the invoice date [1]."), text
    assert "Quoted from the documents" not in text
    [call] = armed_openvault.chat_calls
    shown = "\n".join(m["content"] for m in call["body"]["messages"])
    assert PAYMENT in shown and DOC_HIT_Q in shown
    assert documents.NOT_IN_DOCUMENTS in shown


def test_model_saying_not_in_documents_serves_no_document(armed_openvault, dms_http) -> None:  # noqa: F811
    armed_openvault.reply(documents.NOT_IN_DOCUMENTS)

    body = _ask(dms_http)

    assert _badge(body) != "document", body
    assert "acme_agreement.txt" not in _sources(body)
    assert "acme_agreement.txt" not in (body.get("answer") or "")
    assert len(armed_openvault.chat_calls) == 1


def test_model_citing_an_unseen_passage_is_replaced_by_the_quote(armed_openvault, dms_http) -> None:  # noqa: F811
    armed_openvault.reply("Payment is Net 60 [7].")

    text = _assert_document(_ask(dms_http))

    assert "Net 60" not in text, text
    assert f'"{PAYMENT}" [1]' in text, text
    assert "model answer cited no given passage" in text, text


def test_no_model_quotes_the_passage_and_says_why(dms_http) -> None:  # noqa: F811
    text = _assert_document(_ask(dms_http))

    assert f'"{PAYMENT}" [1]' in text, text
    assert "Quoted from the documents; no model summarised them (" in text, text


def test_data_question_never_gets_an_unrelated_document(armed_openvault, dms_http) -> None:  # noqa: F811
    """No passage carries the question's terms, so nothing is served and no model is asked."""
    body = _ask(dms_http, DATA_Q)

    assert _badge(body) != "document", body
    assert "acme_agreement.txt" not in _sources(body)
    assert "Acme" not in (body.get("answer") or "")
    assert armed_openvault.chat_calls == []


# -- retrieval ------------------------------------------------------------------


def test_bm25_ranks_the_passage_holding_the_rare_terms_first() -> None:
    passages = [
        documents.Passage("a.txt", "Acme supplier overview. Acme ships fasteners."),
        documents.Passage("b.txt", "Payment terms are Net 30 for Acme invoices."),
        documents.Passage("c.txt", "Warehouse opening hours."),
    ]

    top = documents.retrieve("what are the Acme payment terms", passages)

    assert [p.source for p in top] == ["b.txt"]


def test_relevance_needs_two_question_terms() -> None:
    assert documents.is_relevant("acme payment terms", "Payment terms: Net 30.")
    assert not documents.is_relevant("list skus in stock today", "Stock is counted weekly.")
    assert not documents.is_relevant("what is it", "anything at all")


def test_chunk_keeps_lines_whole_under_the_cap() -> None:
    text = "\n".join(f"line {i} " + "x" * 50 for i in range(30))

    passages = documents.chunk("d.txt", text, max_chars=200)

    assert len(passages) > 1
    assert all(len(p.text) <= 200 for p in passages)
    assert "\n".join(p.text for p in passages) == text


def test_cited_answer_rejects_a_citation_outside_the_passages() -> None:
    assert documents.cited_answer("Net 30 [1].", 2) == "Net 30 [1]."
    assert documents.cited_answer("Net 30 [3].", 2) is None
    assert documents.cited_answer("Net 30.", 2) is None


def test_a_quote_needs_half_the_question_covered() -> None:
    """With no model to judge answerability, two matching words are not enough."""
    passages = documents.chunk(
        "acme.txt", "Minimum order quantity: 50 units per SKU.\nReorder when stock falls."
    )

    assert documents.retrieve("stock turnover per SKU last quarter", passages)
    assert documents.answer("stock turnover per SKU last quarter", passages, None) is None
    found = documents.answer("minimum order quantity per SKU", passages, None)
    assert found is not None and found.method == "extractive"
    assert '"Minimum order quantity: 50 units per SKU." [1]' in found.answer


def test_contract_ask_document_answer_claims_no_warehouse_table(dms_http) -> None:  # noqa: F811
    """/v1/contract/ask has no document badge, so the answer falls closed to
    ABSTAIN; its source cards must not then list the warehouse grant."""
    dms_http.bind_session(SESSION, {"transactions": "TRUE", "inventory": "TRUE"}, space_id=SPACE)

    resp = dms_http.post(
        "/v1/contract/ask", json={"question": DOC_HIT_Q, "session_id": SESSION, "space_id": SPACE}
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["provenance"]["badge"] == "abstain", body
    assert f'"{PAYMENT}" [1]' in body["answer"], body["answer"]
    assert "Sources: [1] acme_agreement.txt" in body["answer"]
    assert body["contributing_sources"] == [], body["contributing_sources"]
    assert body.get("rows") in ([], None) and body.get("sql_used") is None
