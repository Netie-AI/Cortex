"""ANS-01, second pass: an adverb between the entity and the verb.

The first fix ended the exclusion clause *negatively* - it popped trailing
tokens that appeared in ``_EXCLUSION_SKIP``. Two lists could still disagree,
just later: any word neither list knew stayed in the clause, the fuzzy resolver
could not match it exactly, and the engine asked the customer to confirm an
exclusion they had already stated precisely. Six phrasings abstained, two of
them carrying no punctuation at all, so a punctuation fix could not reach them.

The clause now ends *positively*, at the entities that actually resolve, so an
unknown adverb cannot break it (R-0004 - no list decides).

Every assertion here is on the rendered answer text and the returned rows
(R-0001, CLAUDE.md section 8). The test that shipped with the first fix built
its rendered string from ``rows`` rather than ``answer``, and could pass
vacuously when ``rows == []`` because ``"SKU-BETA" not in ""`` is true - it
stayed green with the fix neutralised. The row-count precondition below is what
stops that from happening again.
"""

from __future__ import annotations

import pytest

from CortexOS.dms.answer_engine import answer

#: Adverbs that sat between the entity and the verb. None of them appears on
#: any stop list, which is the point - the span must not depend on one.
ADVERB_PHRASINGS = [
    "ignore SKU-BETA and show the top 5 SKUs by revenue",
    "ignore SKU-BETA and also show the top 5 SKUs by revenue",
    "ignore SKU-BETA and just show the top 5 SKUs by revenue",
    "exclude SKU-BETA and kindly show the top 5 SKUs by revenue",
    "exclude SKU-BETA and maybe show the top 5 SKUs by revenue",
    "exclude SKU-BETA and simply show the top 5 SKUs by revenue",
    "ignore SKU-BETA and perhaps show the top 5 SKUs by revenue",
    "exclude SKU-BETA and thereafter show the top 5 SKUs by revenue",
    "ignore BETA and GAMMA and show the top 5 SKUs by revenue",
    "ignore BETA, GAMMA and show the top 5 SKUs by revenue",
    "keluarkan BETA dari top 5 sku revenue",
    "buang BETA dan tunjukkan top 5 sku revenue",
]


@pytest.mark.parametrize("question", ADVERB_PHRASINGS)
def test_an_adverb_after_the_entity_does_not_force_a_confirm(question: str) -> None:
    out = answer(question, session_id="ans01-adverb")
    rows = out.get("rows") or []

    # Precondition, not decoration: with no rows the "excluded" assertion below
    # is vacuously true, which is how the previous gate stayed green.
    assert rows, (
        f"{question!r} abstained instead of applying the exclusion - "
        f"route={out.get('route')!r} answer={out.get('answer')!r}"
    )
    assert out.get("badge") != "abstain"

    rendered = str(out.get("answer") or "")
    assert "SKU-BETA" not in rendered.upper(), (
        f"the excluded SKU is still named in the answer text: {rendered!r}"
    )
    assert all("BETA" not in str(r.get("sku", "")).upper() for r in rows)


def test_the_exclusion_actually_changes_the_ranking() -> None:
    """Non-vacuous proof: dropping the rank-1 SKU must change the rank-1 row.

    "SKU-BETA is absent" can hold because the exclusion applied *or* because
    that SKU was never in the top 5 to begin with. Excluding the SKU that is
    demonstrably first removes that ambiguity.
    """
    baseline = answer("show the top 5 SKUs by revenue", session_id="ans01-base")
    baseline_skus = [r.get("sku") for r in (baseline.get("rows") or [])]
    assert baseline_skus, "baseline ranking is empty, nothing to exclude from"
    leader = baseline_skus[0]

    out = answer(
        f"ignore {leader} and also show the top 5 SKUs by revenue",
        session_id="ans01-drop",
    )
    rows = out.get("rows") or []
    assert rows, f"excluding {leader} abstained: {out.get('answer')!r}"
    got = [r.get("sku") for r in rows]
    assert leader not in got, f"{leader} survived its own exclusion: {got}"
    assert leader not in str(out.get("answer") or "")


def test_two_named_skus_are_both_excluded() -> None:
    """A conjunction joins entities; both sides must actually be dropped.

    Truncating the span at the first entity applied only one exclusion and
    still answered green - a wrong answer under a success badge, which is worse
    than an abstain.
    """
    baseline = answer("show the top 5 SKUs by revenue", session_id="ans01-base2")
    baseline_skus = [r.get("sku") for r in (baseline.get("rows") or [])]
    assert len(baseline_skus) >= 2
    first, second = baseline_skus[0], baseline_skus[1]

    out = answer(
        f"exclude {first} and {second} and show top 5", session_id="ans01-two"
    )
    rows = out.get("rows") or []
    assert rows, f"two-SKU exclusion abstained: {out.get('answer')!r}"
    got = [r.get("sku") for r in rows]
    assert first not in got and second not in got, (
        f"expected both {first} and {second} dropped, got {got}"
    )


def test_a_named_sku_the_warehouse_does_not_know_abstains_rather_than_half_applying() -> None:
    """R-0011 / CLAUDE.md section 8: never answer with a partial exclusion.

    "SKU-GAMMA" is shaped like a SKU but the warehouse does not encode it.
    Silently dropping it and answering with the other two exclusions applied
    would be a wrong answer wearing a success badge, so the engine abstains and
    names the token it could not match.
    """
    out = answer(
        "exclude SKU-BETA and SKU-GAMMA and SKU-00397 and show top 5",
        session_id="ans01-unknown",
    )
    assert out.get("badge") == "abstain"
    assert not (out.get("rows") or [])
    assert "SKU-GAMMA" in str(out.get("answer") or "")


def test_a_fuzzy_phrase_still_asks_before_it_filters() -> None:
    """The span must not narrow a phrase into an exact match inside it.

    "beta trial" resolves fuzzily to SKU-BETA. Narrowing the clause to the
    "beta" inside it would silently filter something the customer only
    approximately named - exactly what the clarify path exists to prevent.
    """
    out = answer("remove beta trial from the top 5 sales", session_id="ans01-fuzzy")
    assert out.get("route") == "needs_clarification"
    assert out.get("badge") == "abstain"
    assert out.get("sql_used") is None
