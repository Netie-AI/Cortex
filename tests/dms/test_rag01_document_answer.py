"""RAG-01: a document answer is a cited answer from relevant passages, or nothing.

The stub this replaces returned the first 400 characters of any file sharing one
word with the question. The quote fallback that followed (the best
keyword-matching sentences, served under badge=document when the model failed or
cited nothing) let a word-overlap rule choose what was served. That fallback is
gone too: with no model citation there is no document answer. The turn then
falls through to the engine, which abstains or, since #424, reconfirms (CLAUDE.md
section 9: a direct abstain is only for ungranted tables or destructive SQL).
Either way nothing from the documents is served and the badge is not green.

Assertions are on the served envelopes (CLAUDE.md s8): ``POST /dms/query`` and
``POST /v1/contract/ask``. They check the badge, rows, sources, source cards and
rendered answer text, plus the passages the model was shown.
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
CITED = "Invoices are payable Net 30 from the invoice date [1]."


@pytest.fixture(autouse=True)
def _corpus() -> None:
    if not CONTRACTS_DIR.is_dir() or not any(CONTRACTS_DIR.glob("*.txt")):
        pytest.fail(f"RAG-01 needs the sample documents in {CONTRACTS_DIR}")


def _ask(client: Any, question: str = DOC_HIT_Q) -> dict[str, Any]:
    client.bind_session(SESSION, {"transactions": "TRUE"}, space_id=SPACE)
    return _ask_dms(client, question, SESSION, SPACE)


def _contract_ask(client: Any, question: str = DOC_HIT_Q) -> dict[str, Any]:
    client.bind_session(SESSION, {"transactions": "TRUE", "inventory": "TRUE"}, space_id=SPACE)
    resp = client.post(
        "/v1/contract/ask", json={"question": question, "session_id": SESSION, "space_id": SPACE}
    )
    assert resp.status_code == 200, resp.text
    return dict(resp.json())


def _assert_document(body: dict[str, Any]) -> str:
    assert _badge(body) == "document", body
    assert body.get("layer") == "rag" and body.get("route") == "rag", body
    assert body.get("rows") in ([], None)
    assert body.get("sql_used") is None
    assert _sources(body) == ["acme_agreement.txt"], body
    text = body.get("answer") or ""
    assert "Sources: [1] acme_agreement.txt" in text, text
    return text


NOT_SERVED = ("abstain", "reconfirm")


def _assert_no_document_text(text: str) -> None:
    assert "can't answer" in text or text.startswith("Reconfirm:"), text
    assert PAYMENT not in text and "Net 30" not in text, text
    assert "acme_agreement.txt" not in text and "Acme" not in text, text
    assert "Quoted from the documents" not in text, text


def _assert_abstained(body: dict[str, Any]) -> None:
    """``POST /dms/query``: an abstain or reconfirm, not a document answer of any kind."""
    assert _badge(body) in NOT_SERVED, body
    assert body.get("layer") in NOT_SERVED and body.get("route") != "rag", body
    assert body.get("rows") in ([], None) and body.get("sql_used") is None, body
    assert "acme_agreement.txt" not in _sources(body), body
    _assert_no_document_text(body.get("answer") or "")


def _assert_contract_abstained(body: dict[str, Any]) -> None:
    """``POST /v1/contract/ask``: abstain or reconfirm badge and no document source card."""
    assert body["provenance"]["badge"] in NOT_SERVED, body
    assert body["provenance"]["layer"] in NOT_SERVED, body
    assert body.get("route") != "rag", body
    cards = body.get("contributing_sources") or []
    assert not [c for c in cards if c.get("kind") == "document"], cards
    assert "acme_agreement.txt" not in {c.get("ref_id") for c in cards}, cards
    assert body.get("rows") in ([], None) and body.get("sql_used") is None, body
    assert body.get("drillthrough_token") is None
    assert body.get("audit_id")
    _assert_no_document_text(body.get("answer") or "")


# -- a model citation is served -------------------------------------------------


def test_model_answer_is_served_with_its_citation_and_source(armed_openvault, dms_http) -> None:  # noqa: F811
    armed_openvault.reply(CITED)

    text = _assert_document(_ask(dms_http))

    assert text.startswith(CITED), text
    assert "Quoted from the documents" not in text
    [call] = armed_openvault.chat_calls
    shown = "\n".join(m["content"] for m in call["body"]["messages"])
    assert PAYMENT in shown and DOC_HIT_Q in shown
    assert documents.NOT_IN_DOCUMENTS in shown


def test_contract_ask_model_citation_is_badged_document(armed_openvault, dms_http) -> None:  # noqa: F811
    """1.7.0: the cited document answer is Badge.DOCUMENT, and its source cards
    name that document, never the warehouse grant."""
    armed_openvault.reply(CITED)

    body = _contract_ask(dms_http)

    assert body["provenance"]["badge"] == "document", body
    assert body["provenance"]["layer"] == "rag", body
    assert body["answer"] == f"{CITED}\nSources: [1] acme_agreement.txt", body["answer"]
    cards = body["contributing_sources"]
    assert [(c["ref_id"], c["kind"], c["container"]) for c in cards] == [
        ("acme_agreement.txt", "document", "documents")
    ], cards
    assert body.get("rows") in ([], None) and body.get("sql_used") is None
    assert body.get("drillthrough_token") is None
    assert body.get("audit_id")
    assert len(armed_openvault.chat_calls) == 1


# -- anything short of a model citation serves nothing --------------------------


def test_model_saying_not_in_documents_serves_no_document(armed_openvault, dms_http) -> None:  # noqa: F811
    armed_openvault.reply(documents.NOT_IN_DOCUMENTS)

    _assert_abstained(_ask(dms_http))

    assert len(armed_openvault.chat_calls) == 1


def test_model_citing_an_unseen_passage_serves_nothing(armed_openvault, dms_http) -> None:  # noqa: F811
    """A citation outside the passages is not replaced by a keyword quote."""
    armed_openvault.reply("Payment is Net 60 [7].")

    body = _contract_ask(dms_http)

    _assert_contract_abstained(body)
    assert "Net 60" not in body["answer"], body["answer"]
    assert len(armed_openvault.chat_calls) == 1


@pytest.mark.parametrize(
    "reply",
    ["Payment is Net 30 from the invoice date.", ""],
    ids=["uncited", "empty-not-accepted"],
)
def test_model_reply_without_a_citation_serves_nothing(
    armed_openvault, dms_http, reply: str  # noqa: F811
) -> None:
    armed_openvault.reply(reply)

    _assert_contract_abstained(_contract_ask(dms_http))

    assert armed_openvault.chat_calls


def test_model_failure_abstains_with_no_document_card(armed_openvault, dms_http) -> None:  # noqa: F811
    armed_openvault.reply(status=500, error_type="server_error", message="upstream down")

    _assert_contract_abstained(_contract_ask(dms_http))

    assert len(armed_openvault.chat_calls) == 1


def test_no_model_abstains_with_no_document_card(dms_http) -> None:  # noqa: F811
    """FreeRoute is off: nothing judges the passages, so nothing is served."""
    _assert_abstained(_ask(dms_http))
    _assert_contract_abstained(_contract_ask(dms_http))


def test_legacy_bridge_document_question_serves_no_quote(dms_http) -> None:  # noqa: F811
    """``/dms/query``'s pre-Q2 bridge re-routes a "late" question that names a
    document through ``rag_answer`` a second time. With no model it used to serve
    the keyword quote (the agreement header and the reorder line, which do not
    answer it). Now nothing from the documents is served."""
    question = "according to the Acme agreement, what happens when a delivery is late?"
    dms_http.bind_session(SESSION, {"transactions": "TRUE", "shipments": "TRUE"}, space_id=SPACE)

    body = _ask_dms(dms_http, question, SESSION, SPACE)

    assert _badge(body) != "document", body
    assert _sources(body) == [], body
    text = body.get("answer") or ""
    assert "Acme" not in text and "acme_agreement.txt" not in text, text
    assert "Quoted from the documents" not in text, text
    assert body.get("rows") in ([], None) and body.get("sql_used") is None, body


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


# -- answer(): the model's citation or nothing ------------------------------------


_ACME = documents.chunk(
    "acme.txt", "Minimum order quantity: 50 units per SKU.\nReorder when stock falls."
)
_MOQ_Q = "minimum order quantity per SKU"


def _model(reply: documents.ModelReply) -> tuple[documents.Complete, list[Any]]:
    seen: list[Any] = []

    def complete(msgs: list[dict[str, str]]) -> documents.ModelReply:
        seen.append(msgs)
        return reply

    return complete, seen


def test_answer_serves_the_cited_model_reply_with_its_sources() -> None:
    complete, seen = _model(documents.ModelReply(ok=True, text="50 units per SKU [1]."))

    found = documents.answer(_MOQ_Q, _ACME, complete)

    assert found == documents.DocAnswer(
        answer="50 units per SKU [1].\nSources: [1] acme.txt", sources=["acme.txt"], method="model"
    )
    assert len(seen) == 1


@pytest.mark.parametrize(
    "reply",
    [
        documents.ModelReply(ok=False, reason="model unavailable"),
        documents.ModelReply(ok=False, text="50 units per SKU [1].", reason="not accepted"),
        documents.ModelReply(ok=True, text=documents.NOT_IN_DOCUMENTS),
        documents.ModelReply(ok=True, text="The minimum order is 50 units per SKU."),
        documents.ModelReply(ok=True, text="The minimum order is 50 units per SKU [2]."),
        documents.ModelReply(ok=True, text=""),
    ],
    ids=["failed", "not-accepted", "not-in-documents", "uncited", "unseen-cite", "empty"],
)
def test_answer_serves_nothing_without_a_valid_model_citation(reply: documents.ModelReply) -> None:
    """The passage covers the question, so a word-overlap quote would have been served."""
    assert documents.retrieve(_MOQ_Q, _ACME)
    complete, seen = _model(reply)

    assert documents.answer(_MOQ_Q, _ACME, complete) is None
    assert len(seen) == 1


def test_answer_with_no_model_serves_nothing() -> None:
    assert documents.retrieve(_MOQ_Q, _ACME)

    assert documents.answer(_MOQ_Q, _ACME, None) is None


def test_answer_asks_no_model_when_no_passage_is_relevant() -> None:
    complete, seen = _model(documents.ModelReply(ok=True, text="Net 30 [1]."))

    assert documents.answer("warehouse opening hours on holidays", _ACME, complete) is None
    assert seen == []
