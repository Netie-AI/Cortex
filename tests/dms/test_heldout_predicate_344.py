"""HELDOUT-PREDICATE (#344): a dropped filter cannot score correct by luck.

The #313 held-out run scored ``spider_country_mean_lead_having`` correct while
the served SQL had no ``HAVING AVG(lead_time_days) > 20 AND COUNT(*) >= 3``:
every country in the demo database happens to pass it. A result-set match on
one database cannot tell a missing predicate from a correct one.

``score_envelope`` now also runs the gold SQL and the served SQL on seeded
variants of the tables they read (row samples and numeric jitter, see
``bench.heldout.VARIANTS``) and is correct only if they agree on every one.
"""

from __future__ import annotations

from typing import Any

import pytest
import sqlglot
from sqlglot import exp

from bench import heldout
from bench.heldout import HeldoutItem, load_heldout, score_envelope

ITEM_ID = "spider_country_mean_lead_having"
# The served SQL the issue names: the HAVING is gone.
NO_HAVING = "SELECT country FROM suppliers GROUP BY country ORDER BY country"


@pytest.fixture(scope="module", autouse=True)
def _db() -> None:
    from bench.accuracy import _ensure_db_loaded

    _ensure_db_loaded()


def _item(item_id: str = ITEM_ID) -> HeldoutItem:
    return next(i for i in load_heldout() if i.id == item_id)


def _envelope(sql: str) -> dict[str, Any]:
    """The envelope a governed serve of ``sql`` would carry on the demo database."""
    from bench.accuracy import _run_canonical

    rows = _run_canonical(sql)
    return {
        "answer": "Countries: " + ", ".join(str(r.get("country")) for r in rows),
        "rows": rows,
        "badge": "governed_metric",
        "route": "governed_metric",
        "sql_used": sql,
    }


def _gold(item: HeldoutItem) -> str:
    from CortexOS.dms.warehouse_db import DMS_PACK
    from CortexOS.ontology.value_domain import canonical_gold_sql

    return canonical_gold_sql(str(item.canonical_sql), DMS_PACK)


def test_the_issue_case_matches_on_the_base_database() -> None:
    """The precondition of #344: on the one database the dropped HAVING is invisible."""
    from bench.accuracy import _multiset_equal, _project, _run_canonical

    item = _item()
    keys = list(item.key_columns)
    gold = _project(_run_canonical(_gold(item)), keys, item.round)
    served = _project(_run_canonical(NO_HAVING), keys, item.round)
    assert gold and _multiset_equal(served, gold)


def test_must_fail_the_dropped_having_scores_incorrect_on_a_named_variant() -> None:
    result = score_envelope(_item(), _envelope(NO_HAVING))
    assert result.outcome == "incorrect", result
    assert result.detail.startswith("variant_mismatch:"), result.detail
    assert result.detail.split(":", 1)[1] in {v.id for v in heldout.VARIANTS}


def test_must_pass_the_sql_with_its_having_scores_correct() -> None:
    item = _item()
    result = score_envelope(item, _envelope(_gold(item)))
    assert result.outcome == "correct", result


def test_guard_variants_removed_the_dropped_having_scores_correct(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without the variants, main's rule is back and the luck passes."""
    monkeypatch.setattr(heldout, "VARIANTS", ())
    monkeypatch.setattr(
        heldout, "variant_mismatch", lambda item, sql, **kw: None
    )
    assert score_envelope(_item(), _envelope(NO_HAVING)).outcome == "correct"


def test_must_fail_a_served_answer_with_no_sql_cannot_be_checked() -> None:
    env = _envelope(NO_HAVING)
    env.pop("sql_used")
    result = score_envelope(_item(), env)
    assert result.outcome == "incorrect"
    assert result.detail == "variant_unverifiable:no_sql_used"


def test_variants_are_deterministic_and_differ_from_the_base() -> None:
    from CortexOS.dms.warehouse_db import DEFAULT_DB

    def snapshot(variant: heldout.Variant) -> list[tuple[Any, ...]]:
        con = heldout._variant_connection(variant, ["suppliers"], DEFAULT_DB)
        try:
            return con.execute("SELECT * FROM suppliers ORDER BY ALL").fetchall()
        finally:
            con.close()

    from bench.accuracy import _run_canonical

    base = len(_run_canonical("SELECT * FROM suppliers"))
    ids = [v.id for v in heldout.VARIANTS]
    assert len(ids) == len(set(ids)) >= 6
    for variant in heldout.VARIANTS:
        first, second = snapshot(variant), snapshot(variant)
        assert first == second, variant.id
        if variant.kind == "keep":
            assert len(first) < base, variant.id


@pytest.mark.parametrize(
    "item", [i for i in load_heldout() if i.canonical_sql], ids=lambda i: i.id
)
def test_must_pass_every_gold_sql_scores_correct_against_itself(item: HeldoutItem) -> None:
    """No false incorrect: the gold SQL agrees with itself on every variant."""
    assert heldout.variant_mismatch(item, _gold(item)) is None


def _drop_predicates(sql: str) -> str | None:
    """``sql`` with every WHERE and HAVING removed, or None when it has none."""
    tree = sqlglot.parse_one(sql, read="duckdb")
    dropped = False
    for node in list(tree.find_all(exp.Where, exp.Having)):
        node.pop()
        dropped = True
    return tree.sql(dialect="duckdb") if dropped else None


def test_dropped_predicates_are_caught_across_the_held_out_set() -> None:
    """For every gold SQL with a WHERE or HAVING, the predicate-dropped SQL is
    refused by the base match or by a variant; the issue's case only by a variant."""
    from bench.accuracy import _multiset_equal, _project, _run_canonical

    caught_by_variant: list[str] = []
    missed: list[str] = []
    for item in load_heldout():
        if not item.canonical_sql:
            continue
        gold = _gold(item)
        mutant = _drop_predicates(gold)
        if mutant is None:
            continue
        keys = list(item.key_columns)
        base_same = _multiset_equal(
            _project(_run_canonical(mutant), keys, item.round),
            _project(_run_canonical(gold), keys, item.round),
        )
        if not base_same:
            continue
        if heldout.variant_mismatch(item, mutant):
            caught_by_variant.append(item.id)
        else:
            missed.append(item.id)
    assert ITEM_ID in caught_by_variant
    assert missed == [], f"dropped predicates no variant tells apart: {missed}"
