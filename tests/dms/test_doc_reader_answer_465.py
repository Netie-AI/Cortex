"""#465: the document reader's reply is served only when it is a finished answer.

``cited_answer`` used to serve any reply carrying an in-range ``[n]``. Reasoning
aloud ("Passage [1] mentions ... so the answer should be"), often cut off at the
token cap, was served under badge ``document``: a WRONG=0 gap with
``CORTEX_DOC_RANK`` off too.

Now the reader states its answer as one ``{"answer", "citations"}`` JSON object
and only ``answer`` is served, and a reply the provider reports as not finished
(``finish_reason`` ``length`` or any value other than ``stop``) serves nothing.
No phrase list decides it. A rejected reply falls through to the path a document
miss takes; it does not abstain or reconfirm on its own.

Assertions are on what the consumer receives (CLAUDE.md s8): ``POST /dms/query``,
``POST /v1/contract/ask`` with the lane router off and on.
"""

from __future__ import annotations

import json
import re
from typing import Any

import pytest

from CortexOS.dms import lane_ask
from CortexOS.integrations import freeroute
from CortexOS.rag import documents
from tests.dms.reader_reply import reader_reply
from tests.dms.test_lane_router import DOC_REPLY, DOCUMENTS, _check_prompts, _doc_prompts, _line
from tests.dms.test_lane_router import _ok as _router_ok
from tests.dms.test_rag01_document_answer import (  # noqa: F401
    CITED,
    _assert_abstained,
    _assert_contract_abstained,
    _ask,
    _contract_ask,
    _corpus,
    dms_http,
)
from tests.dms.test_rag03_answer_routing import DOC_HIT_Q

# A reply cut off at the token cap while the model reasons aloud. It cites [1].
TRUNCATED = (
    "Let me look at the passages. Passage [1] is the Acme supplier agreement and "
    "it mentions payment terms. The question asks for the payment terms, so I need "
    "to check whether [1] says Net 30 or Net"
)
# Reasoning aloud that did finish. It cites [1] and ends on a figure.
REASONING = (
    "The question asks for the payment terms in the Acme agreement. Passage [1] "
    "covers the agreement; I think it lists Net 60, but it may mean something "
    "else, so the answer is probably Net 60 [1]."
)
# The answer object a well-behaved reader sends.
ANSWER = reader_reply(CITED)

# (reply text, finish_reason) the reader may send; none of them is an answer.
NOT_ANSWERS: dict[str, tuple[str, str | None]] = {
    "truncated-reasoning-length": (TRUNCATED, "length"),
    "truncated-reasoning-no-finish-reason": (TRUNCATED, None),
    "reasoning-finished": (REASONING, "stop"),
    "reasoning-no-finish-reason": (REASONING, None),
    "reasoning-then-object": (f"{REASONING}\n{ANSWER}", "stop"),
    "object-then-reasoning": (f"{ANSWER}\nI am not sure [1] is the right passage.", "stop"),
    "object-cut-off": (ANSWER[: len(ANSWER) - 12], "length"),
    "object-cut-off-no-finish-reason": (ANSWER[: len(ANSWER) - 12], None),
    "complete-object-but-length": (ANSWER, "length"),
    "complete-object-but-content-filter": (ANSWER, "content_filter"),
    "reasoning-inside-a-string": (json.dumps(REASONING), "stop"),
    "object-with-extra-reasoning-key": (
        json.dumps({"answer": CITED, "citations": [1], "reasoning": "[1] says Net 30"}),
        "stop",
    ),
    "answer-not-a-string": (json.dumps({"answer": ["Net 30 [1]"], "citations": [1]}), "stop"),
    "citations-empty": (reader_reply(CITED, []), "stop"),
    "citation-a-bool": (json.dumps({"answer": CITED, "citations": [True]}), "stop"),
    "citation-a-string": (json.dumps({"answer": CITED, "citations": ["1"]}), "stop"),
    "inline-cite-not-listed": (reader_reply("Net 30 [1] and Net 60 [2].", [2]), "stop"),
    "no-inline-cite": (reader_reply("Invoices are payable Net 30.", [1]), "stop"),
    "unseen-passage": (reader_reply("Invoices are payable Net 30 [9]."), "stop"),
    "answer-says-not-in-documents": (
        reader_reply(f"{documents.NOT_IN_DOCUMENTS} [1]"),
        "stop",
    ),
}
_SERVED_PARTS = ("Net 30", "Net 60", "Passage [1]", "Acme", "acme_agreement.txt")

_PASSAGES = documents.chunk("acme.txt", "Payment terms: Net 30 from invoice date.\nOther.")
_PAY_Q = "what are the Acme payment terms from the invoice date"


def _read(text: str, finish: str | None) -> documents.DocAnswer | None:
    top = documents.retrieve(_PAY_Q, _PASSAGES)
    assert top, "the passage must clear the gate, or the test proves nothing"
    reply = documents.ModelReply(ok=True, text=text, finish_reason=finish or "")
    return documents.read(_PAY_Q, top, lambda _msgs: reply)


def _script(fake: Any, text: str, finish: str | None) -> None:
    """Queue one chat reply carrying ``finish_reason`` (the fake's ``reply`` has no such field)."""
    choice: dict[str, Any] = {"message": {"role": "assistant", "content": text}}
    if finish is not None:
        choice["finish_reason"] = finish
    fake.replies.append((200, {"choices": [choice]}))


def _old_cited_answer(text: str, n_passages: int) -> str | None:
    """``cited_answer`` as it was on main: any in-range ``[n]`` was an answer."""
    body = (text or "").strip()
    cites = {int(c) for c in re.findall(r"\[(\d+)\]", body)}
    if not body or not cites or not cites <= set(range(1, n_passages + 1)):
        return None
    return body


# -- the reader function ----------------------------------------------------


@pytest.mark.parametrize("name", list(NOT_ANSWERS))
def test_must_fail_reader_reply_that_is_not_a_finished_answer_serves_nothing(name: str) -> None:
    text, finish = NOT_ANSWERS[name]
    assert _read(text, finish) is None


@pytest.mark.parametrize(
    "text",
    [ANSWER, f"```json\n{ANSWER}\n```", f"  {ANSWER}\n", reader_reply("Net 30 [1].", [1, 1])],
    ids=["object", "fenced", "whitespace", "repeated-citation"],
)
@pytest.mark.parametrize("finish", ["stop", None])
def test_must_pass_finished_answer_object_serves_only_its_answer(
    text: str, finish: str | None
) -> None:
    found = _read(text, finish)
    assert found is not None
    answer = json.loads(text.strip().strip("`").removeprefix("json"))["answer"]
    assert found.answer == f"{answer}\nSources: [1] acme.txt"
    assert found.sources == ["acme.txt"]
    assert "{" not in found.answer and "citations" not in found.answer


def test_reader_prompt_asks_for_the_answer_object() -> None:
    system = documents.messages(_PAY_Q, _PASSAGES)[0]["content"]
    assert '{"answer":' in system and '"citations":' in system
    assert documents.NOT_IN_DOCUMENTS in system


def test_guard_finish_reason_removed_serves_a_cut_off_object() -> None:
    """The finish-reason guard is what refuses a complete object cut at the cap."""
    text, finish = NOT_ANSWERS["complete-object-but-length"]
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(documents, "finished", lambda reply: True)
        assert _read(text, finish) is not None
    assert _read(text, finish) is None


@pytest.mark.parametrize(
    "name",
    ["truncated-reasoning-length", "reasoning-finished", "reasoning-then-object"],
)
def test_guard_old_citation_rule_serves_reasoning(name: str) -> None:
    """With main's rule back, reasoning aloud with a citation is served again."""
    text, finish = NOT_ANSWERS[name]
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(documents, "cited_answer", _old_cited_answer)
        mp.setattr(documents, "finished", lambda reply: True)
        served = _read(text, finish)
    assert served is not None and served.answer.startswith(text.strip())


# -- FreeRoute carries the provider's finish reason ----------------------------


@pytest.mark.parametrize("finish", ["length", "stop", None])
def test_freeroute_completion_carries_the_finish_reason(armed_openvault, finish) -> None:
    _script(armed_openvault, "text", finish)
    out = freeroute.complete(documents.TASK_DOC_ANSWER, [{"role": "user", "content": "q"}])
    assert out.ok
    assert out.finish_reason == (finish or "")


def test_doc_model_passes_the_finish_reason_to_the_reader(armed_openvault) -> None:
    from CortexOS.dms.query_service import _doc_model

    _script(armed_openvault, ANSWER, "length")
    reply = _doc_model([{"role": "user", "content": "q"}])
    assert reply.ok and reply.finish_reason == "length"


# -- what the consumer receives -------------------------------------------------


@pytest.mark.parametrize("name", list(NOT_ANSWERS))
def test_must_fail_dms_query_serves_no_document_for_a_non_answer(
    armed_openvault, dms_http, name: str  # noqa: F811
) -> None:
    text, finish = NOT_ANSWERS[name]
    _script(armed_openvault, text, finish)

    body = _ask(dms_http)

    _assert_abstained(body)
    for part in _SERVED_PARTS:
        assert part not in (body.get("answer") or ""), (part, body.get("answer"))
    assert len(_doc_prompts(armed_openvault)) == 1


@pytest.mark.parametrize("name", list(NOT_ANSWERS))
def test_must_fail_contract_ask_serves_no_document_for_a_non_answer(
    armed_openvault, dms_http, name: str  # noqa: F811
) -> None:
    text, finish = NOT_ANSWERS[name]
    _script(armed_openvault, text, finish)

    body = _contract_ask(dms_http)

    _assert_contract_abstained(body)
    for part in _SERVED_PARTS:
        assert part not in (body.get("answer") or ""), (part, body.get("answer"))
    assert len(_doc_prompts(armed_openvault)) == 1


@pytest.mark.parametrize("finish", ["stop", None])
def test_must_pass_contract_ask_serves_a_finished_answer_object(
    armed_openvault, dms_http, finish  # noqa: F811
) -> None:
    _script(armed_openvault, ANSWER, finish)

    body = _contract_ask(dms_http)

    assert body["provenance"]["badge"] == "document", body
    assert body["answer"] == f"{CITED}\nSources: [1] acme_agreement.txt", body["answer"]
    assert "citations" not in body["answer"]


@pytest.mark.parametrize(
    "name",
    ["truncated-reasoning-length", "reasoning-finished", "complete-object-but-length"],
)
def test_must_fail_lane_router_falls_through_on_a_non_answer(
    armed_openvault, dms_http, monkeypatch: pytest.MonkeyPatch, name: str  # noqa: F811
) -> None:
    """A rejected reply is a document miss: no answer check, the ladder runs."""
    monkeypatch.setenv(lane_ask.ENABLED_ENV, "1")
    text, finish = NOT_ANSWERS[name]
    armed_openvault.reply(DOCUMENTS)
    _script(armed_openvault, text, finish)

    body = _router_ok(dms_http, DOC_HIT_Q)

    assert _line(body) == "lane: other (model:documents-miss; calls=1)"
    assert body["provenance"]["badge"] != "document", body
    assert body.get("route") != "rag"
    cards = body.get("contributing_sources") or []
    assert not [c for c in cards if c.get("kind") == "document"], cards
    for part in _SERVED_PARTS:
        assert part not in (body.get("answer") or ""), (part, body.get("answer"))
    assert _check_prompts(armed_openvault) == []
    # The router's reader call, then the ladder's own document step after its miss.
    assert len(_doc_prompts(armed_openvault)) >= 1


def test_must_pass_lane_router_still_serves_a_finished_answer(
    armed_openvault, dms_http, monkeypatch: pytest.MonkeyPatch  # noqa: F811
) -> None:
    monkeypatch.setenv(lane_ask.ENABLED_ENV, "1")
    armed_openvault.reply(DOCUMENTS)
    _script(armed_openvault, reader_reply(DOC_REPLY), "stop")
    armed_openvault.reply('{"answers":true}')

    body = _router_ok(dms_http, DOC_HIT_Q)

    assert _line(body) == "lane: documents (model; calls=1)"
    assert body["provenance"]["badge"] == "document"
    assert body["answer"] == f"{DOC_REPLY}\nSources: [1] acme_agreement.txt"
