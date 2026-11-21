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
        "answer": "Rows: " + "; ".join(", ".join(str(v) for v in r.values()) for r in rows),
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
    monkeypatch.setattr(heldout, "variant_mismatch", lambda item, sql, **kw: None)
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


@pytest.mark.parametrize("item", [i for i in load_heldout() if i.canonical_sql], ids=lambda i: i.id)
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


# ── attacker round on 1a2fd87 ───────────────────────────────────────────────
# Wrong SQLs that matched gold on the base and on every keep / jitter / skew
# variant: those only drop rows and scale numbers, so a string, date or sign
# the base never holds never appears.
WRONG_ON_NO_SAMPLED_VARIANT = {
    "cutoff_two_days_late": (
        "bird_stale_audit_with_delayed_inbound",
        "SELECT COUNT(*) AS supplier_count FROM suppliers s "
        "WHERE CAST(s.last_audit_date AS DATE) < DATE '2024-01-03' AND EXISTS "
        "(SELECT 1 FROM shipments sh WHERE sh.supplier_id = s.supplier_id "
        "AND sh.status = 'DELAYED')",
    ),
    "cutoff_inside_an_empty_gap": (
        "bird_stale_audit_with_delayed_inbound",
        "SELECT COUNT(*) AS supplier_count FROM suppliers s "
        "WHERE CAST(s.last_audit_date AS DATE) <= DATE '2023-12-28' AND EXISTS "
        "(SELECT 1 FROM shipments sh WHERE sh.supplier_id = s.supplier_id "
        "AND sh.status = 'DELAYED')",
    ),
    "dropped_positive_reorder_level": (
        "bird_cold_bin_below_reorder",
        "SELECT COUNT(*) AS bin_count FROM inventory i JOIN locations l "
        "ON l.location_id = i.location_id "
        "WHERE l.is_cold_storage = true AND i.quantity_kg < i.reorder_level_kg",
    ),
    "status_by_excluding_the_others": (
        "bird_delayed_hazardous_to_cold",
        "SELECT COUNT(*) AS delayed_count FROM shipments sh JOIN (SELECT sku, "
        "MAX(CASE WHEN is_hazardous THEN 1 ELSE 0 END) AS haz FROM inventory GROUP BY sku) i "
        "ON i.sku = sh.sku AND i.haz = 1 JOIN locations l "
        "ON l.location_id = sh.destination_location_id WHERE sh.status NOT IN "
        "('PENDING','IN_TRANSIT','CANCELLED','DELIVERED') AND l.is_cold_storage = true",
    ),
    "severity_by_excluding_the_others": (
        "spider_unresolved_critical_slow_supplier",
        "SELECT COUNT(*) AS alert_count FROM alerts a JOIN suppliers s "
        "ON s.supplier_id = a.related_supplier WHERE a.resolved = false "
        "AND a.severity NOT IN ('HIGH','MEDIUM','LOW') AND s.lead_time_days > 30",
    ),
}

# Different SQL, same meaning: must stay correct.
RIGHT_IN_ANOTHER_FORM = {
    "avg_form": (
        "bird_carrier_delay_rate_above_global",
        "SELECT c.carrier FROM (SELECT carrier, AVG(CASE WHEN status='DELAYED' THEN 1.0 "
        "ELSE 0.0 END) r FROM shipments GROUP BY carrier) c WHERE c.r > (SELECT "
        "AVG(CASE WHEN status='DELAYED' THEN 1.0 ELSE 0.0 END) FROM shipments) ORDER BY 1",
    ),
    "sum_over_count": (
        "spider_avg_cost_delayed_hazardous",
        "SELECT ROUND(SUM(sh.cost_myr)/COUNT(*), 2) AS avg_cost_myr FROM shipments sh "
        "WHERE sh.status = 'DELAYED' AND sh.sku IN (SELECT sku FROM inventory WHERE is_hazardous)",
    ),
    "cte_shadows_the_table": (
        "spider_country_mean_lead_having",
        "WITH suppliers AS (SELECT * FROM main.suppliers) SELECT country FROM suppliers "
        "GROUP BY country HAVING AVG(lead_time_days) > 20 AND COUNT(*) >= 3 ORDER BY country",
    ),
    "in_subqueries": (
        "bird_delayed_hazardous_to_cold",
        "SELECT COUNT(*) AS delayed_count FROM shipments sh WHERE sh.status = 'DELAYED' "
        "AND sh.sku IN (SELECT sku FROM inventory WHERE is_hazardous) "
        "AND sh.destination_location_id IN (SELECT location_id FROM locations "
        "WHERE is_cold_storage)",
    ),
    "join_to_a_dimension_the_gold_does_not_read": (
        "spider_avg_cost_delayed_hazardous",
        "SELECT ROUND(AVG(sh.cost_myr), 2) AS avg_cost_myr FROM shipments sh "
        "JOIN locations l ON l.location_id = sh.destination_location_id "
        "WHERE sh.status = 'DELAYED' AND EXISTS (SELECT 1 FROM inventory i "
        "WHERE i.sku = sh.sku AND i.is_hazardous = true)",
    ),
}


def _base_match(item: HeldoutItem, sql: str) -> bool:
    from bench.accuracy import _multiset_equal, _project, _run_canonical

    keys = list(item.key_columns)
    return _multiset_equal(
        _project(_run_canonical(sql), keys, item.round),
        _project(_run_canonical(_gold(item)), keys, item.round),
    )


@pytest.mark.parametrize("case", sorted(WRONG_ON_NO_SAMPLED_VARIANT))
def test_must_fail_a_wrong_sql_no_sampled_variant_moves_is_caught(case: str) -> None:
    item_id, sql = WRONG_ON_NO_SAMPLED_VARIANT[case]
    item = _item(item_id)
    assert _base_match(item, sql), "precondition: the base database cannot tell"
    sampled = tuple(v for v in heldout.VARIANTS if v.kind in ("keep", "jitter", "skew"))
    assert heldout.variant_mismatch(item, sql, variants=sampled) is None, "precondition"
    result = score_envelope(item, _envelope(sql))
    assert result.outcome == "incorrect", (case, result)
    assert result.detail.split(":", 1)[1].split("-")[0] in {"novel0.3", "edge0.3"}, result


@pytest.mark.parametrize("case", sorted(RIGHT_IN_ANOTHER_FORM))
def test_must_pass_a_right_sql_in_another_form_scores_correct(case: str) -> None:
    item_id, sql = RIGHT_IN_ANOTHER_FORM[case]
    item = _item(item_id)
    assert _base_match(item, sql), "precondition"
    assert heldout.variant_mismatch(item, sql) is None, case


def _where_dropped(item_id: str) -> str:
    tree = sqlglot.parse_one(_gold(_item(item_id)), read="duckdb")
    for node in list(tree.find_all(exp.Where)):
        node.pop()
    return tree.sql(dialect="duckdb")


CROSS_TABLE = "bird_inbound_shipments_gt_sku_distinct"


@pytest.mark.parametrize(
    "spell",
    [
        lambda sql: sql,
        lambda sql: sql.replace("FROM shipments", "FROM SHIPMENTS"),
        lambda sql: (
            sqlglot.parse_one(sql, read="duckdb")
            .join("(SELECT COUNT(*) AS zc FROM alerts) AS z", on="TRUE", join_type="LEFT")
            .sql(dialect="duckdb")
        ),
    ],
    ids=["as_is", "table_in_capitals", "extra_table_sorted_first"],
)
def test_must_fail_how_the_served_sql_spells_tables_cannot_move_the_skew(spell) -> None:  # noqa: ANN001
    """The skew bit index ran over the served SQL's tables too, so an extra
    table or a capitalised name moved the thinned table and the dropped WHERE
    scored correct."""
    item = _item(CROSS_TABLE)
    sql = spell(_where_dropped(CROSS_TABLE))
    assert _base_match(item, sql), "precondition"
    assert heldout.variant_mismatch(item, sql) is not None


def test_lanes_grading_passes_the_served_sql_to_the_scorer() -> None:
    """``bench.lanes`` built the view without ``sql_used``: every data item it
    graded was ``variant_unverifiable``, a correct answer graded WRONG."""
    from bench import lanes

    item = _item()
    lane_item = lanes.LaneItem(
        id=item.id,
        split="sql",
        kind="data",
        lane="sql",
        accept=("rows",),
        question=item.question,
        canonical_sql=item.canonical_sql,
        key_columns=tuple(item.key_columns),
        round=item.round,
        match=item.match,
    )

    def body(sql: str) -> dict[str, Any]:
        env = _envelope(sql)
        return {
            "answer": env["answer"],
            "rows": env["rows"],
            "provenance": {"badge": "governed_metric"},
            "sql_used": sql,
        }

    right = lanes._grade_data(lane_item, body(_gold(item)))
    wrong = lanes._grade_data(lane_item, body(NO_HAVING))
    assert right.outcome == lanes.RIGHT, right
    assert wrong.outcome == lanes.WRONG, wrong


def test_shadow_l2_line_carries_its_sql() -> None:
    env = heldout._shadow_l2_env({"l2_sql": "SELECT 1", "l2_values": [{"a": 1}]})
    assert env["sql_used"] == "SELECT 1"


def test_one_item_that_raises_while_scoring_is_named_not_fatal(monkeypatch) -> None:  # noqa: ANN001
    def boom(item, sql, **kw):  # noqa: ANN001
        raise RuntimeError("variant build failed")

    monkeypatch.setattr(heldout, "variant_mismatch", boom)
    result = heldout._score_or_error(_item(), _envelope(NO_HAVING))
    assert result.outcome == "incorrect"
    assert result.detail == "score raised RuntimeError: variant build failed"


# Verifier on 0a99832: equal on every lake the engine serves, so never incorrect.
GOLD_REWRITES = {
    # Each SKU has one category on the base: a per-row change broke that.
    "any_value_for_min_under_a_dependency": (
        "bird_out_kg_above_category_mean",
        ("MIN(category)", "ANY_VALUE(category)"),
    ),
    "max_for_min_under_a_dependency": (
        "bird_out_kg_above_category_mean",
        ("MIN(category)", "MAX(category)"),
    ),
    # txn_type has a declared value domain: a lake holding any other value is
    # refused (#402), so excluding the other declared values is the same filter.
    "excluding_the_other_declared_values": (
        "spider_delayed_sku_never_sold",
        ("t.txn_type = 'outbound'", "t.txn_type NOT IN ('inbound', 'ADJUST', 'WRITE_OFF')"),
    ),
}


@pytest.mark.parametrize("case", sorted(GOLD_REWRITES))
def test_must_pass_a_rewrite_equal_on_every_served_lake_scores_correct(case: str) -> None:
    item_id, (old, new) = GOLD_REWRITES[case]
    item = _item(item_id)
    gold = _gold(item)
    assert old in gold, "precondition: the rewrite applies"
    sql = gold.replace(old, new)
    assert _base_match(item, sql), "precondition"
    assert heldout.variant_mismatch(item, sql) is None, case


# Re-verify on 138df5c: renaming a whole value can take the gold's own filter
# literal, and the gold's aggregate then says "nothing" ([(None,)]) where an
# equal rewrite says it as no rows. Only that pair agrees: a zero is a value
# and a NULL row where the gold has none is a phantom (re-verify on d063c33).
SECOND_HIGHEST = "spider_second_highest_chem_unit_cost"
EMPTY_ON_A_VARIANT = {
    "dense_rank": (
        "SELECT unit_cost_myr FROM (SELECT DISTINCT ROUND(unit_cost_myr,2) unit_cost_myr "
        "FROM inventory WHERE category='CHEMICALS') d "
        "QUALIFY dense_rank() OVER (ORDER BY unit_cost_myr DESC)=2"
    ),
    "limit_offset": (
        "SELECT DISTINCT ROUND(unit_cost_myr,2) AS unit_cost_myr FROM inventory "
        "WHERE category='CHEMICALS' ORDER BY 1 DESC LIMIT 1 OFFSET 1"
    ),
}


@pytest.mark.parametrize("case", sorted(EMPTY_ON_A_VARIANT))
def test_must_pass_no_rows_and_a_null_are_the_same_empty_answer(case: str) -> None:
    item = _item(SECOND_HIGHEST)
    sql = EMPTY_ON_A_VARIANT[case]
    assert _base_match(item, sql), "precondition"
    assert heldout.variant_mismatch(item, sql) is None, case


STILL_CAUGHT = {
    "rate_ge_for_gt": (
        "bird_carrier_delay_rate_above_global",
        ("c.delay_rate > g.global_rate", "c.delay_rate >= g.global_rate"),
    ),
    "severity_in_for_eq": (
        "bird_over_capacity_with_critical_alert",
        ("a.severity = 'CRITICAL'", "a.severity IN ('CRITICAL','HIGH')"),
    ),
}


@pytest.mark.parametrize("case", sorted(STILL_CAUGHT))
def test_must_fail_a_wrong_sql_with_rows_where_gold_is_empty_is_caught(case: str) -> None:
    item_id, (old, new) = STILL_CAUGHT[case]
    item = _item(item_id)
    gold = _gold(item)
    assert old in gold, "precondition: the rewrite applies"
    sql = gold.replace(old, new)
    assert _base_match(item, sql), "precondition"
    assert heldout.variant_mismatch(item, sql) is not None, case


NOT_THE_SAME_NOTHING = {
    "made_up_zero_average": (
        "spider_avg_cost_delayed_hazardous",
        ("ROUND(AVG(sh.cost_myr), 2)", "COALESCE(ROUND(AVG(sh.cost_myr), 2), 0)"),
    ),
    "made_up_zero_second_highest": (
        "spider_second_highest_chem_unit_cost",
        (
            "SELECT MAX(unit_cost_myr) AS unit_cost_myr",
            "SELECT COALESCE(MAX(unit_cost_myr), 0) AS unit_cost_myr",
        ),
    ),
    "no_row_for_a_zero_count": (
        "bird_delayed_hazardous_to_cold",
        ("l.is_cold_storage = true", "l.is_cold_storage = true HAVING COUNT(*) > 0"),
    ),
}


@pytest.mark.parametrize("case", sorted(NOT_THE_SAME_NOTHING))
def test_must_fail_a_zero_or_a_missing_count_is_not_the_gold_s_nothing(case: str) -> None:
    item_id, (old, new) = NOT_THE_SAME_NOTHING[case]
    item = _item(item_id)
    gold = _gold(item)
    assert old in gold, "precondition: the rewrite applies"
    sql = gold.replace(old, new)
    assert _base_match(item, sql), "precondition"
    assert heldout.variant_mismatch(item, sql) is not None, case


def test_must_fail_a_phantom_null_row_where_the_gold_has_none_is_caught() -> None:
    item = _item("spider_country_mean_lead_having")
    sql = (
        "SELECT q.country FROM (SELECT 1) d LEFT JOIN ("
        + _gold(item).replace("ORDER BY country", "")
        + ") q ON true"
    )
    assert _base_match(item, sql), "precondition"
    assert heldout.variant_mismatch(item, sql) is not None
