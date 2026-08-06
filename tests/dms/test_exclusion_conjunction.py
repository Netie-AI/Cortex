"""ANS-01 — naming an exact SKU before a conjunction must not force a confirm chip.

Reproduced before the fix, all three answering `needs_clarification` with zero
rows:

    "ignore SKU-BETA and show the top 5 SKUs by revenue"
    "ignore BETA and show the top 5 SKUs by revenue"
    "keluarkan BETA dari top 5 sku revenue"          (Malay)

Two lists disagreed about one word. `_EXCLUSION_STOP` decides where the entity
clause ends and omitted `and` / `dan`, so `_exclusion_clauses` handed the
resolver the literal string `"SKU-BETA and"`. `_EXCLUSION_SKIP` — used by
`_excluded_skus` — does contain them, so the token path got it right and the
clause path did not. The fuzzy resolver could not match `"sku-beta and"`
exactly, so it asked the user to confirm an exclusion they had already stated
precisely.

That is an R-0005 refusal of legitimate work, and it contradicts the documented
rule that an exact encoding (`BETA` → `SKU-BETA`) applies immediately.

Adding `and|dan` to the stop list would fix these three strings and leave the
class alive: two lists that can drift apart again. The fix is at the funnel —
the clause is stripped of trailing skip tokens before it reaches the resolver,
so the lists cannot disagree (R-0004).

Assertions are on the rendered answer and returned rows, never the SQL.
"""

from __future__ import annotations

import pytest

from CortexOS.dms.answer_engine import ABSTAIN, _exclusion_clauses, clear_session
from CortexOS.dms.query_service import answer_question

REPRODUCTIONS = [
    "ignore SKU-BETA and show the top 5 SKUs by revenue",
    "ignore BETA and show the top 5 SKUs by revenue",
    "keluarkan BETA dari top 5 sku revenue",
]


@pytest.fixture(autouse=True)
def _clean(request):
    clear_session(f"ans01-{request.node.name}")


@pytest.mark.parametrize("question", REPRODUCTIONS)
def test_exclusion_before_a_conjunction_answers(question: str) -> None:
    """The customer-visible outcome: rows, not a confirm chip."""
    result = answer_question(question)

    assert result["route"] != ABSTAIN, result.get("answer")
    assert result.get("rows"), "an exact SKU exclusion must answer, not abstain"


@pytest.mark.parametrize("question", REPRODUCTIONS)
def test_the_excluded_sku_is_actually_gone(question: str) -> None:
    """Applying the exclusion matters more than merely not abstaining.

    An answer that ignored the exclusion would also pass the test above, so
    this asserts the named SKU is absent from the rows the customer receives.
    """
    result = answer_question(question)

    rendered = " ".join(str(v) for row in (result.get("rows") or []) for v in row.values())
    assert "SKU-BETA" not in rendered.upper()


@pytest.mark.parametrize(
    "clause_text",
    ["SKU-BETA and", "BETA and", "BETA dari", "SKU-BETA and the", "BETA or"],
)
def test_trailing_filler_never_reaches_the_resolver(clause_text: str) -> None:
    """The funnel itself — where the two lists used to disagree.

    Pinned directly so the fix cannot regress into the stop-list, which is
    where the divergence lived.
    """
    clauses = _exclusion_clauses(f"ignore {clause_text} show top 5 skus by revenue")

    for clause in clauses:
        last = clause.split()[-1].upper() if clause.split() else ""
        assert last not in {"AND", "OR", "DARI", "DAN", "THE"}, (
            f"clause {clause!r} still ends in filler — the resolver will fuzzy-match it"
        )


def test_a_real_multi_sku_exclusion_still_parses() -> None:
    """R-0005 control: stripping trailing filler must not eat a named SKU.

    "ignore BETA and GAMMA" is a two-SKU exclusion — the `and` is joining
    entities, not ending the clause, and both must survive.
    """
    clauses = _exclusion_clauses("ignore BETA and GAMMA show top 5 skus by revenue")

    joined = " ".join(clauses).upper()
    assert "BETA" in joined and "GAMMA" in joined
