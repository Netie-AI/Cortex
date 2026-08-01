"""Asking what *will* happen is refused; asking what is *scheduled* is not.

The guard was ``\\b(forecast|predict|projection|what if|hypothetical)\\b`` — a
list of ways to say it rather than the class. Measured on the corpus, 4 of 7
forecast paraphrases walked straight past it and L2 answered each with
historically-valid SQL over the past: confidently wrong about the future,
badged as a real answer.

The hard part is the other direction. ``shipments.expected_arrival`` and
``inventory.expiry_date`` are stored future dates, so "which deliveries are due
in the next seven days" and "which SKUs expire next month" are lookups, not
predictions. A guard that refuses those is a control rejecting legitimate work
(R-0005), and the corpus could not have caught it: every expiry seed in it is
past tense.
"""

from __future__ import annotations

import pytest
from CortexOS.dms.answer_engine import _is_predictive

# The four that escaped the old five-word list, plus the two it did catch.
PREDICTIVE = [
    "forecast next quarter demand for SKU-00173",
    "what will demand be next quarter for SKU-00173",
    "project SKU-00173 demand for next quarter",
    "how much of SKU-00173 will we sell next quarter",
    "estimate next quarter demand for SKU-00173",
    "predict revenue for the coming month",
    "what if we double the reorder level",
    "simulate a 10% price rise",
    "hypothetical: WH-A closes next week",
]

# Recorded dates that happen to be in the future. These must still answer.
SCHEDULED = [
    "which deliveries are due in the next seven days",
    "what shipments arrive next week",
    "which SKUs expire next month",
    "what stock expires next week",
    "which shipments are expected at WH-A next month",
    "what is due for restock next week",
]

# Ordinary historical questions — the guard must be invisible to them.
HISTORICAL = [
    "top 5 selling SKUs by revenue",
    "which SKUs are below reorder level",
    "total revenue last quarter",
    "which items expired last month",
    "show shipment cost by destination",
    "how many suppliers are in Malaysia",
    "what is our distinct SKU count",
]


@pytest.mark.parametrize("question", PREDICTIVE)
def test_the_future_is_refused(question: str) -> None:
    assert _is_predictive(question), f"L2 would answer {question!r} from historical rows"


@pytest.mark.parametrize("question", SCHEDULED)
def test_a_stored_future_date_is_not_a_prediction(question: str) -> None:
    """R-0005. expiry_date and expected_arrival are columns, not guesses."""
    assert not _is_predictive(question), f"refused {question!r}, which the warehouse records"


@pytest.mark.parametrize("question", HISTORICAL)
def test_ordinary_questions_are_untouched(question: str) -> None:
    assert not _is_predictive(question)


def test_both_call_sites_share_one_detector() -> None:
    """The regex was duplicated at two call sites and could drift (R-0004)."""
    import inspect

    from CortexOS.dms import answer_engine

    src = inspect.getsource(answer_engine)
    assert src.count('r"\\b(forecast|predict|projection|what if|hypothetical)\\b"') == 0, (
        "the old inline literal is back — both sites must call _is_predictive"
    )
