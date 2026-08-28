"""Destructive-intent routing — both directions.

The old detector was `\\b(drop|delete|truncate|alter|insert|update|create)\\b`
over raw natural language. It failed both ways, and both failures are captured
here so neither can come back:

  * false POSITIVE — "update me on the delayed shipments" and "cost by drop-off
    point" were refused as destructive. A refusal on an ordinary business
    question is the most expensive kind of wrong for an analyst-facing tool.
  * false NEGATIVE — "wipe all supplier records" sailed past as a normal query.
    No data was ever at risk (sql_guardrail's AST check is the real
    enforcement), but the destructive INTENT was never detected, so it was never
    refused with a cause and never landed in the audit trail as an attempt.
"""
from __future__ import annotations

import pytest

from CortexOS.dms.query_service import destructive_intent, route_question

MUST_BLOCK = [
    # literal SQL
    "Drop table inventory",
    "delete from suppliers where 1=1",
    "truncate inventory",
    "update inventory set quantity_kg = 0",
    "insert into inventory values (1)",
    "alter table shipments add column x",
    # plain English — the whole class the old detector missed
    "wipe all supplier records",
    "remove the inventory table",
    "erase everything in inventory",
    "delete every row in suppliers",
    "purge the transactions table",
    "clear all alerts",
    "reset all records",
    "destroy the warehouse data",
    "overwrite the supplier records",
    "Delete all records from suppliers",
]

MUST_NOT_BLOCK = [
    # the false positives that made the tool unusable for real questions
    "update me on the delayed shipments",
    "keep me updated on late deliveries",
    "any updates on shipment X",
    "when was inventory last updated",
    "what does shipping cost us by drop-off point",
    "show me the drop-off schedule",
    "which items dropped in price",
    "create a report of sales by category",
    "create a chart of capacity utilisation",
    # predicate-adjective / passive uses of a mutation verb
    "which locations are clear of alerts",
    "which records were deleted last week",
    # ordinary golden-set questions
    "Which items are expired?",
    "List active alerts across the warehouse network",
    "Show SKU count by category",
    "Rank suppliers by combined risk and lead time score",
]


@pytest.mark.parametrize("question", MUST_BLOCK)
def test_destructive_requests_are_blocked(question: str) -> None:
    assert route_question(question) == "blocked", question


@pytest.mark.parametrize("question", MUST_NOT_BLOCK)
def test_ordinary_questions_are_not_blocked(question: str) -> None:
    assert route_question(question) != "blocked", question


@pytest.mark.parametrize("question", MUST_BLOCK)
def test_refusal_carries_an_auditable_cause(question: str) -> None:
    """A refusal must say WHY — a bare boolean is not an audit record."""
    reason = destructive_intent(question)
    assert reason, question
    kind, _, evidence = reason.partition(":")
    assert kind in ("sql_write_statement", "mutation_intent")
    assert evidence.strip(), f"no evidence captured for {question!r}"


def test_empty_and_whitespace_are_not_destructive() -> None:
    assert destructive_intent("") is None
    assert destructive_intent("   ") is None


def test_answer_path_refuses_plain_english_destruction() -> None:
    """End-to-end: the engine, not just the classifier, must refuse."""
    from CortexOS.dms.query_service import answer_question

    result = answer_question("wipe all supplier records")
    assert result["route"] == "blocked"
    assert result["violations_blocked"] == ["DDL_ATTEMPT"]
    assert not result.get("rows")


# ── document routing ─────────────────────────────────────────────────────────
# `RAG_KEYWORDS` used to fire on the bare openers "what does" / "explain", so
# "what does shipping cost us by destination" was answered out of the supplier
# contract corpus — a confident zero-row answer to an analytics question.

DOCUMENT_QUESTIONS = [
    "what does the SOP document say about cold chain?",
    "according to the supplier agreement, what is the lead time",
    "explain the payment terms in our contract",
    "what do the contracts say about penalties",
    "under the supplier agreement, who pays freight",
]

ANALYTICS_QUESTIONS = [
    "what does shipping cost us by drop-off point",
    "what does our inventory look like by category",
    "explain the drop in sales last month",
    "which suppliers have a risk score above 0.7?",
    "what is our total spend by supplier country?",
]


@pytest.mark.parametrize("question", DOCUMENT_QUESTIONS)
def test_document_questions_route_to_rag(question: str) -> None:
    assert route_question(question) == "rag", question


@pytest.mark.parametrize("question", ANALYTICS_QUESTIONS)
def test_analytics_questions_never_route_to_rag(question: str) -> None:
    """Wrong-corpus answers are worse than abstaining: an analytics question
    answered from the contract corpus looks confident and returns nothing."""
    assert route_question(question) != "rag", question
