"""#465: a document answer is served only when it is a finished answer a second call confirms.

``cited_answer`` used to serve any reader reply carrying an in-range ``[n]``.
Reasoning aloud ("Passage [1] mentions ... so the answer should be"), often cut
off at the token cap, was served under badge ``document``: a WRONG=0 gap with
``CORTEX_DOC_RANK`` off too.

Two gates now, and no phrase list:

* Form. The reader replies with one ``{"answer", "citations"}`` object and only
  ``answer`` is served; a reply the provider reports as not finished, a repeated
  key, an answer with no text besides its citations, a ``Sources:`` line the
  model wrote, or the miss sentinel in any spelling serves nothing.
* A check. A second call sees the question, the passages and the answer, and
  must reply a strict ``{"answers": true}``. The three-attacker round showed
  that the form alone serves reasoning, hedges and contradictions placed inside
  ``answer``, and that the lane router's own check never sees the passages and
  is skipped when the ladder re-reads after a fall-through.

A rejected reply falls through as a document miss; it does not abstain or
reconfirm on its own. Assertions are on what the consumer receives
(CLAUDE.md s8): ``POST /dms/query`` and ``POST /v1/contract/ask`` with the lane
router off and on.
"""

from __future__ import annotations

import json
import re
from typing import Any

import pytest

from CortexOS.dms import lane_ask
from CortexOS.integrations import freeroute
from CortexOS.rag import documents
from tests.dms.reader_reply import (
    DOC_CHECK_NO,
    DOC_CHECK_OK,
    answer_doc_checks,
    confirm_doc_checks,  # noqa: F401 - armed tests get their checks confirmed
    is_doc_check,
    reader_reply,
)
from tests.dms.test_lane_router import DOC_REPLY, DOCUMENTS, _check_prompts, _doc_prompts, _line
from tests.dms.test_lane_router import _ok as _router_ok
from tests.dms.test_rag01_document_answer import (  # noqa: F401
    CITED,
    _ask,
    _assert_abstained,
    _assert_contract_abstained,
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
ANSWER = reader_reply(CITED)

# (reply text, finish_reason): refused by the form gate, before any check.
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
    "repeated-answer-key": (
        '{"answer": "Net 30 [1].", "citations": [1], "answer": "Hmm, [1] might say Net 60"}',
        "stop",
    ),
    "repeated-citations-key": ('{"answer": "Net 30 [1].", "citations": [5], "citations": [1]}', "stop"),
    "answer-not-a-string": (json.dumps({"answer": ["Net 30 [1]"], "citations": [1]}), "stop"),
    "citation-only": (reader_reply("[1]"), "stop"),
    "citation-and-dots-only": (reader_reply(" ... [1] ."), "stop"),
    "citations-empty": (reader_reply(CITED, []), "stop"),
    "citation-a-bool": (json.dumps({"answer": CITED, "citations": [True]}), "stop"),
    "citation-a-string": (json.dumps({"answer": CITED, "citations": ["1"]}), "stop"),
    "citation-zero-padded": (json.dumps({"answer": "Net 30 [01].", "citations": [1]}), "stop"),
    "citation-non-ascii-digit": (json.dumps({"answer": "Net 30 [١].", "citations": [1]}), "stop"),
    "inline-cite-not-listed": (reader_reply("Net 30 [1] and Net 60 [2].", [2]), "stop"),
    "no-inline-cite": (reader_reply("Invoices are payable Net 30.", [1]), "stop"),
    "unseen-passage": (reader_reply("Invoices are payable Net 30 [9]."), "stop"),
    "own-sources-line": (reader_reply("Net 90 [1].\nSources: [1] master_agreement.pdf"), "stop"),
    "sentinel-in-answer": (reader_reply(f"{documents.NOT_IN_DOCUMENTS} [1]"), "stop"),
    "sentinel-lowercase": (reader_reply("not_in_documents [1]"), "stop"),
    "sentinel-spaced": (reader_reply("Not in documents [1]."), "stop"),
}
# Well-formed objects whose ``answer`` is not an answer. Only the check refuses them.
CHECK_REFUSES: dict[str, str] = {
    "reasoning-inside-answer": reader_reply(REASONING),
    "truncated-inside-answer": reader_reply(TRUNCATED),
    "hedge": reader_reply("The agreement does not state the payment terms [1]."),
    "contradiction": reader_reply("Payment terms are Net 60 from invoice date [1]."),
    "other-document-named": reader_reply("Per the Globex master agreement, terms are Net 45 [1]."),
}
_SERVED_PARTS = ("Net 30", "Net 60", "Net 45", "Net 90", "Passage [1]", "Acme", "acme_agreement.txt")

_PASSAGES = documents.chunk("acme.txt", "Payment terms: Net 30 from invoice date.\nOther.")
_PAY_Q = "what are the Acme payment terms from the invoice date"


def _check(verdict: str = DOC_CHECK_OK, finish: str = "stop", ok: bool = True) -> tuple[Any, list[Any]]:
    seen: list[Any] = []

    def check(msgs: list[dict[str, str]]) -> documents.ModelReply:
        seen.append(msgs)
        return documents.ModelReply(ok=ok, text=verdict, finish_reason=finish)

    return check, seen


def _read(text: str, finish: str | None, check: Any = None) -> documents.DocAnswer | None:
    top = documents.retrieve(_PAY_Q, _PASSAGES)
    assert top, "the passage must clear the gate, or the test proves nothing"
    reply = documents.ModelReply(ok=True, text=text, finish_reason=finish or "")
    return documents.read(_PAY_Q, top, lambda _msgs: reply, check or _check()[0])


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


def _no_document(body: dict[str, Any]) -> None:
    for part in _SERVED_PARTS:
        assert part not in (body.get("answer") or ""), (part, body.get("answer"))


# -- the form gate ---------------------------------------------------------------


@pytest.mark.parametrize("name", list(NOT_ANSWERS))
def test_must_fail_reader_reply_that_is_not_a_finished_answer_serves_nothing(name: str) -> None:
    text, finish = NOT_ANSWERS[name]
    check, seen = _check()
    assert _read(text, finish, check) is None
    assert seen == [], "a reply the form gate refuses never reaches the check"


@pytest.mark.parametrize(
    "text",
    [
        ANSWER,
        f"```json\n{ANSWER}\n```",
        f"```JSON\n{ANSWER}\n```",
        f"  {ANSWER}\n",
        f"<think>Passage [1] has the terms.</think>\n{ANSWER}",
        reader_reply(CITED, [1, 1]),
    ],
    ids=["object", "fenced", "fenced-upper", "whitespace", "think-block", "repeated-citation"],
)
@pytest.mark.parametrize("finish", ["stop", None])
def test_must_pass_finished_confirmed_object_serves_only_its_answer(
    text: str, finish: str | None
) -> None:
    check, seen = _check()
    found = _read(text, finish, check)
    assert found is not None
    assert found.answer == f"{CITED}\nSources: [1] acme.txt"
    assert found.sources == ["acme.txt"]
    assert len(seen) == 1


@pytest.mark.parametrize(
    "answer", ["NOT_IN_DOCUMENTS [1]", "not_in_documents [1]", "Not in documents [1].", "NOT IN DOCUMENTS [1]"]
)
def test_must_fail_cited_answer_refuses_the_miss_sentinel_in_any_spelling(answer: str) -> None:
    """Pinned at the parser too: ``read`` also checks the raw reply, so either layer alone holds."""
    assert documents.cited_answer(reader_reply(answer), 1) is None
    assert documents.says_not_in_documents(answer)


def test_reader_prompt_asks_for_the_answer_object() -> None:
    system = documents.messages(_PAY_Q, _PASSAGES)[0]["content"]
    assert '{"answer":' in system and '"citations":' in system
    assert documents.NOT_IN_DOCUMENTS in system


# -- the check -------------------------------------------------------------------


@pytest.mark.parametrize("name", list(CHECK_REFUSES))
def test_must_fail_well_formed_non_answer_is_served_only_if_the_check_confirms(name: str) -> None:
    """The form gate passes these; a check that says no keeps them out."""
    text = CHECK_REFUSES[name]
    assert _read(text, "stop", _check(DOC_CHECK_NO)[0]) is None
    # The check is what decides: confirmed, the form gate alone would serve it.
    assert _read(text, "stop", _check(DOC_CHECK_OK)[0]) is not None


@pytest.mark.parametrize(
    ("verdict", "finish", "ok"),
    [
        (DOC_CHECK_NO, "stop", True),
        ('{"answers":"true"}', "stop", True),
        ('{"answers":1}', "stop", True),
        ('{"answers":true,"why":"x"}', "stop", True),
        ('{"answers":true,"answers":false}', "stop", True),
        ('{"answers":false,"answers":true}', "stop", True),
        ("yes", "stop", True),
        (f"{DOC_CHECK_OK} because [1] says so", "stop", True),
        (DOC_CHECK_OK, "length", True),
        (DOC_CHECK_OK, "stop", False),
        ("", "stop", True),
    ],
    ids=[
        "false", "string-true", "one", "extra-key", "repeated-true-then-false",
        "repeated-false-then-true", "prose", "trailing-prose", "length", "call-failed", "empty",
    ],
)
def test_must_fail_anything_but_a_strict_confirmation_serves_nothing(
    verdict: str, finish: str, ok: bool
) -> None:
    assert _read(ANSWER, "stop", _check(verdict, finish, ok)[0]) is None


def test_no_check_serves_nothing() -> None:
    top = documents.retrieve(_PAY_Q, _PASSAGES)
    reply = documents.ModelReply(ok=True, text=ANSWER, finish_reason="stop")
    assert documents.read(_PAY_Q, top, lambda _m: reply, None) is None


def test_the_check_is_shown_the_question_the_answer_and_the_passages() -> None:
    check, seen = _check()
    assert _read(ANSWER, "stop", check) is not None
    [msgs] = seen
    shown = "\n".join(m["content"] for m in msgs)
    assert _PAY_Q in shown and CITED in shown
    assert "Payment terms: Net 30 from invoice date." in shown
    assert '{"answers":true}' in msgs[0]["content"]


# -- guards: each goes red with its gate removed ------------------------------------


def test_guard_finish_reason_removed_serves_a_cut_off_object() -> None:
    text, finish = NOT_ANSWERS["complete-object-but-length"]
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(documents, "finished", lambda reply: True)
        assert _read(text, finish) is not None
    assert _read(text, finish) is None


@pytest.mark.parametrize(
    "name", ["truncated-reasoning-length", "reasoning-finished", "reasoning-then-object"]
)
def test_guard_old_citation_rule_serves_reasoning(name: str) -> None:
    """With main's form rule back, reasoning aloud with a citation passes the form gate."""
    text, finish = NOT_ANSWERS[name]
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(documents, "cited_answer", _old_cited_answer)
        mp.setattr(documents, "finished", lambda reply: True)
        served = _read(text, finish)
    assert served is not None and served.answer.startswith(text.strip())


@pytest.mark.parametrize("name", ["reasoning-inside-answer", "hedge", "contradiction"])
def test_guard_check_removed_serves_a_well_formed_non_answer(name: str) -> None:
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(documents, "confirmed", lambda reply: True)
        assert _read(CHECK_REFUSES[name], "stop", _check(DOC_CHECK_NO)[0]) is not None


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


def _doc_checks(fake: Any) -> list[dict[str, Any]]:
    return [c for c in fake.chat_calls if is_doc_check(c["body"])]


@pytest.mark.parametrize("name", list(NOT_ANSWERS))
def test_must_fail_dms_query_serves_no_document_for_a_non_answer(
    armed_openvault, dms_http, name: str  # noqa: F811
) -> None:
    text, finish = NOT_ANSWERS[name]
    _script(armed_openvault, text, finish)

    body = _ask(dms_http)

    _assert_abstained(body)
    _no_document(body)
    assert len(_doc_prompts(armed_openvault)) == 1


@pytest.mark.parametrize("name", list(NOT_ANSWERS))
def test_must_fail_contract_ask_serves_no_document_for_a_non_answer(
    armed_openvault, dms_http, name: str  # noqa: F811
) -> None:
    text, finish = NOT_ANSWERS[name]
    _script(armed_openvault, text, finish)

    body = _contract_ask(dms_http)

    _assert_contract_abstained(body)
    _no_document(body)
    assert len(_doc_prompts(armed_openvault)) == 1
    assert _doc_checks(armed_openvault) == []


@pytest.mark.parametrize("name", list(CHECK_REFUSES))
def test_must_fail_contract_ask_serves_no_document_the_check_refuses(
    armed_openvault, dms_http, monkeypatch: pytest.MonkeyPatch, name: str  # noqa: F811
) -> None:
    answer_doc_checks(monkeypatch, armed_openvault, DOC_CHECK_NO)
    _script(armed_openvault, CHECK_REFUSES[name], "stop")

    body = _contract_ask(dms_http)

    _assert_contract_abstained(body)
    _no_document(body)
    [check] = _doc_checks(armed_openvault)
    assert "Payment terms: Net 30 from invoice date." in json.dumps(check["body"]["messages"])


@pytest.mark.parametrize("finish", ["stop", None])
def test_must_pass_contract_ask_serves_a_confirmed_answer_object(
    armed_openvault, dms_http, finish  # noqa: F811
) -> None:
    _script(armed_openvault, ANSWER, finish)

    body = _contract_ask(dms_http)

    assert body["provenance"]["badge"] == "document", body
    assert body["answer"] == f"{CITED}\nSources: [1] acme_agreement.txt", body["answer"]
    assert "citations" not in body["answer"]
    assert len(_doc_checks(armed_openvault)) == 1


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
    _no_document(body)
    assert _check_prompts(armed_openvault) == []
    # The router's reader call, then the ladder's own document step after its miss.
    assert len(_doc_prompts(armed_openvault)) >= 1


def test_must_fail_ladder_reread_after_a_router_fall_through_is_checked(
    armed_openvault, dms_http, monkeypatch: pytest.MonkeyPatch  # noqa: F811
) -> None:
    """The attacker's case: the router refuses a reply, the ladder reads again and
    the same reply comes back. The second read is checked too, so nothing is served."""
    monkeypatch.setenv(lane_ask.ENABLED_ENV, "1")
    answer_doc_checks(monkeypatch, armed_openvault, DOC_CHECK_NO)
    armed_openvault.reply(DOCUMENTS)
    armed_openvault.default_content = CHECK_REFUSES["hedge"]

    body = _router_ok(dms_http, DOC_HIT_Q)

    assert body["provenance"]["badge"] != "document", body
    _no_document(body)
    reads = len(_doc_prompts(armed_openvault))
    assert reads >= 2, "the ladder must have read the documents again"
    assert len(_doc_checks(armed_openvault)) == reads


def test_must_pass_lane_router_still_serves_a_confirmed_answer(
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
    assert len(_doc_checks(armed_openvault)) == 1
