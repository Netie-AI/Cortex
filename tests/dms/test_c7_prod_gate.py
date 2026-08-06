"""C7-prod — the stages after generation, and the routing defect they exposed.

Three things are asserted here, all on what the customer actually receives
(CLAUDE.md §8: an assertion on generated SQL alone will certify a broken feature
as working):

1. A question naming one SKU never gets answered with a warehouse-wide number.
2. The validation gate does not claim EXPLAIN approved SQL that EXPLAIN never saw.
3. A filter that cannot match anything abstains instead of reporting a real zero.
"""

from __future__ import annotations

import pytest

from CortexOS.dms import sql_plausibility
from CortexOS.dms.answer_engine import answer, route_to_metric
from CortexOS.dms.sql_validate_gate import SqlGateAbstain, gate_with_retry, run_gate

SEMANTIC = {
    "tables": {
        "inventory": {"columns": ["sku", "sku_name", "quantity_kg", "location_id"]},
        "transactions": {"columns": ["sku", "revenue_myr", "txn_date"]},
    }
}


# ---------------------------------------------------------------- routing


@pytest.mark.parametrize(
    "question",
    [
        "total revenue for SKU-00397",
        "what is the total revenue of SKU-00397",
        "total revenue for SKU-DOESNOTEXIST",
    ],
)
def test_named_sku_never_answered_with_a_warehouse_wide_number(question: str) -> None:
    """The live P0: "total revenue for SKU-00397" answered "sku_count = 509".

    ``\\bskus?\\b`` matches inside the identifier, so the population-count branch
    fired. Excluding that one branch moved the same wrong answer to
    ``revenue_total`` — the whole warehouse's revenue — because that branch's
    guard tests normalized text. Both were badged ``governed_metric`` with a
    drillthrough token, which is what makes it a confidently-wrong answer rather
    than a miss.

    There is no per-SKU revenue metric in the pack, so abstain is the only honest
    outcome. Asserting the rendered answer, not the plan, because the plan being
    ``None`` is not what protects the customer.
    """
    result = answer(question)

    assert result["route"] == "needs_clarification"
    assert result["badge"] == "abstain"
    assert result["rows"] == []
    text = result["answer"]
    assert "509" not in text, "answered with the count of every SKU"
    assert "80375993" not in text.replace(",", ""), "answered with warehouse-wide revenue"


@pytest.mark.parametrize(
    ("question", "expected_metric"),
    [
        ("how many SKUs are there", "sku_count"),
        ("count of unique SKUs", "sku_count"),
        ("our distinct SKU count", "sku_count"),
        ("total revenue", "revenue_total"),
    ],
)
def test_population_questions_still_route(question: str, expected_metric: str) -> None:
    """R-0005: the guard must not refuse the questions it was never about.

    Naming no SKU means the guard cannot fire; these are the population-level
    asks that must keep working exactly as before.
    """
    plan = route_to_metric(question)
    assert plan is not None, f"{question!r} stopped routing"
    assert plan.metric_id == expected_metric


def test_exclusion_is_passed_through_by_the_sku_guard() -> None:
    """An exclusion names a SKU *and* is legitimately about it.

    The SKU is resolved into the plan's slots, so the guard passes it through —
    this is the case that would break if the check were "names a SKU, refuse".

    This asserted ``"SKU-BETA" in result["answer"]`` and passed for the wrong
    reason: the answer was the confirm chip *"Do you mean exclude SKU-BETA"*, so
    the string appeared because the engine was refusing to answer (ANS-01).
    With the exclusion applied the query runs and SKU-BETA is correctly **absent**
    from the ranking — the old assertion now fails on the fixed behaviour, which
    is what a test pinning a defect does.

    So it asserts the docstring's actual claim instead: the guard passes the
    exclusion through and the question is answered.
    """
    result = answer("ignore SKU-BETA and show the top 5 SKUs by revenue")

    assert result["badge"] == "governed_metric", result.get("answer")
    assert result["rows"], "the guard refused an exclusion it is supposed to allow"
    rendered = " ".join(str(v) for row in result["rows"] for v in row.values()).upper()
    assert "SKU-BETA" not in rendered, "the exclusion routed but was never applied"


# ------------------------------------------------------------ gate honesty


def test_gate_does_not_claim_explain_ran_without_a_connection() -> None:
    """``explain_ok`` used to be set True on the no-connection path.

    It reads downstream as "EXPLAIN approved this", which is a claim about a
    check that never happened (R-0011). The pass is still a pass — parse and
    allowlist really did succeed — but it must not borrow EXPLAIN's authority.
    """
    gate = run_gate("SELECT sku FROM inventory LIMIT 5", SEMANTIC, con=None)

    assert gate.passed is True
    assert gate.explain_ran is False
    assert gate.explain_ok is False
    assert gate.explain_skipped_reason


def test_require_explain_refuses_when_explain_cannot_run() -> None:
    """Generated SQL sets require_explain: no dry-run, no execution."""
    gate = run_gate(
        "SELECT sku FROM inventory LIMIT 5", SEMANTIC, con=None, require_explain=True
    )

    assert gate.passed is False
    assert any("EXPLAIN_UNAVAILABLE" in v for v in gate.violations)


def test_every_candidate_is_gated_before_a_retry_is_spent() -> None:
    """A provider returning three candidates should not cost three round-trips.

    The loop used to take ``candidates[0]`` and throw the rest away, then pay for
    another generation to ask again. The retry budget is what decides whether the
    path abstains, so spending it on work already done made it abstain earlier
    than it needed to.
    """
    rounds: list[list[str]] = []

    def generate(prior: list[str]) -> list[str]:
        rounds.append(list(prior))
        return [
            "SELECT nonexistent_col FROM inventory",  # UNKNOWN_COLUMN
            "SELECT * FROM not_a_table",  # UNKNOWN_TABLE
            "SELECT sku FROM inventory LIMIT 5",  # valid
        ]

    gate = gate_with_retry(generate, "q", SEMANTIC, con=None, max_retries=2)

    assert gate.passed is True
    assert len(rounds) == 1, "should not have regenerated; a later candidate was valid"


def test_exhausted_retries_abstain_rather_than_return_bad_sql() -> None:
    def generate(prior: list[str]) -> list[str]:
        return ["SELECT * FROM not_a_table"]

    with pytest.raises(SqlGateAbstain):
        gate_with_retry(generate, "q", SEMANTIC, con=None, max_retries=2)


# ------------------------------------------------------------ plausibility


def test_literal_predicates_are_lifted_from_both_polarities() -> None:
    """``NOT IN ('BETA')`` is the same defect as ``= 'BETA'``.

    A value that does not exist makes an exclusion silently do nothing, and the
    customer gets an unfiltered ranking presented as a filtered one.
    """
    preds = sql_plausibility.extract_literal_predicates(
        "SELECT sku FROM inventory WHERE sku NOT IN ('BETA') AND location_id = 'WH-A'"
    )
    found = {(p.column, p.value) for p in preds}
    assert ("sku", "BETA") in found
    assert ("location_id", "WH-A") in found


def test_disjunction_branches_are_not_probed() -> None:
    """One empty branch of an OR does not make the query wrong (R-0005)."""
    preds = sql_plausibility.extract_literal_predicates(
        "SELECT sku FROM inventory WHERE sku = 'BETA' OR sku = 'SKU-BETA'"
    )
    assert preds == []


def test_numbers_and_dates_are_left_alone() -> None:
    preds = sql_plausibility.extract_literal_predicates(
        "SELECT sku FROM inventory WHERE quantity_kg = 5"
    )
    assert preds == []


def test_impossible_filter_is_reported_with_candidates() -> None:
    """The A-0003 case: filter matches nothing, and the answer must not be zero."""

    def runner(sql: str) -> list[dict]:
        if "LIKE" in sql.upper():
            return [{"sku": "SKU-BETA"}]
        return []  # the probed value exists nowhere

    result = sql_plausibility.check(
        "SELECT sum(revenue_myr) FROM transactions WHERE sku = 'BETA'",
        runner,
        layer="generated",
    )

    assert result.ok is False
    assert result.impossible[0].predicate.value == "BETA"
    assert "SKU-BETA" in result.impossible[0].candidates
    assert "SKU-BETA" in result.reason()


def test_present_filter_passes_and_an_honest_zero_survives() -> None:
    """A real value that simply has no matching rows is a true answer.

    The probe asks whether the *value* exists, never whether the query returns
    rows, precisely so "0 delayed shipments" stays answerable.
    """

    def runner(sql: str) -> list[dict]:
        return [{"sku": "SKU-BETA"}]

    result = sql_plausibility.check(
        "SELECT count(*) FROM transactions WHERE sku = 'SKU-BETA'",
        runner,
        layer="generated",
    )
    assert result.ok is True
    assert result.probed == 1


def test_probe_failure_is_disclosed_not_swallowed() -> None:
    """An unavailable probe reports ``skipped_reason`` rather than a clean pass."""

    def runner(sql: str) -> list[dict]:
        raise RuntimeError("warehouse locked")

    result = sql_plausibility.check(
        "SELECT sku FROM inventory WHERE sku = 'BETA'", runner, layer="generated"
    )
    assert result.ok is True
    assert result.skipped_reason and "warehouse locked" in result.skipped_reason


def test_governed_metric_sql_is_not_probed() -> None:
    """Compiled semantic-layer SQL already resolves its values."""
    result = sql_plausibility.check(
        "SELECT sku FROM inventory WHERE sku = 'BETA'", lambda _s: [], layer="governed_metric"
    )
    assert result.ok is True
    assert result.probed == 0


def test_literal_with_a_quote_cannot_break_out_of_the_probe() -> None:
    """The probe is built as an AST, so the literal is re-escaped, not pasted."""
    preds = sql_plausibility.extract_literal_predicates(
        "SELECT sku FROM inventory WHERE sku = 'O''Brien'"
    )
    assert len(preds) == 1
    probe = sql_plausibility._probe_sql(preds[0])
    assert "'O''Brien'" in probe
    assert probe.count("'") % 2 == 0
