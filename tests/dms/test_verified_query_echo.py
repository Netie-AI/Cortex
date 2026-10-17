"""VERIFIED-QUERY (#309) serve-time check and example scope, as pure functions.

The HTTP must-fails in test_verified_query_contract_ask.py prove these on the
served envelope. These pin each rule's edges: what is refused, and the
paraphrases that must still be served.
"""

from __future__ import annotations

from CortexOS.dms import verified_query_ask as vqa

SCHEMA = {
    "shipments": frozenset(
        {"shipment_id", "sku", "supplier_id", "destination_location_id", "quantity_kg", "status", "carrier"}
    ),
    "inventory": frozenset({"sku", "category", "location_id", "quantity_kg", "is_hazardous"}),
    "transactions": frozenset({"txn_id", "sku", "location_id", "txn_type", "quantity_kg", "unit_cost_myr"}),
    "locations": frozenset({"location_id", "location_code"}),
}
DELAYED = {
    "id": "vq_delayed",
    "question": "How many delayed shipments do we have?",
    "sql": "SELECT COUNT(*) AS delayed_count FROM shipments WHERE status = 'DELAYED'",
}
SKUS = {
    "id": "vq_skus",
    "question": "How many SKUs do we have in inventory?",
    "sql": "SELECT COUNT(DISTINCT sku) AS sku_count FROM inventory",
}
LISTING = {
    "id": "vq_list",
    "question": "List the sku and quantity_kg of transactions for SKU-00296",
    "sql": "SELECT sku, quantity_kg FROM transactions WHERE sku = 'SKU-00296'",
}
REVENUE = {
    "id": "vq_revenue",
    "question": "What is our total revenue?",
    "sql": (
        "SELECT ROUND(SUM(quantity_kg * unit_cost_myr), 2) AS revenue_myr "
        "FROM transactions WHERE txn_type = 'outbound'"
    ),
}
HAZARD = {
    "id": "vq_hazard",
    "question": "List hazardous SKUs in inventory",
    "sql": "SELECT sku FROM inventory WHERE is_hazardous = true",
}


def _echo(question: str, sql: str, *examples: dict[str, str]) -> list[str]:
    return vqa.example_echo(question, sql, list(examples), schema=SCHEMA)


# -- value rule ----------------------------------------------------------------
def test_value_from_an_example_must_be_in_the_question() -> None:
    refused = _echo("How many pending shipments do we have?", DELAYED["sql"] + " LIMIT 1000", DELAYED)
    assert refused and all(r.startswith(vqa.ECHO_CODE) for r in refused)
    assert "'delayed'" in refused[0] and "vq_delayed" in refused[0]
    listing = _echo(
        "List the sku and quantity_kg of transactions for SKU-00109", LISTING["sql"], LISTING
    )
    assert any("'sku-00296'" in r for r in listing)


def test_value_spelled_in_the_question_is_served() -> None:
    assert _echo("How many shipments are delayed right now?", DELAYED["sql"], DELAYED) == []
    assert _echo("Show me the sku and quantity_kg for SKU-00296", LISTING["sql"], LISTING) == []


def test_confirmed_question_is_a_re_serve_even_with_an_unspelled_value() -> None:
    """'outbound' is not in the words, but this is the question the steward confirmed."""
    assert _echo("What is our total revenue?", REVENUE["sql"], REVENUE) == []
    assert _echo("what is our TOTAL revenue", REVENUE["sql"], REVENUE) == []
    asked = "What is our total revenue to date?"
    assert vqa.lexical_similarity(asked, REVENUE["question"]) >= vqa.RETRIEVE_MIN
    refused = _echo(asked, REVENUE["sql"], REVENUE)
    assert any("'outbound'" in r for r in refused), "a paraphrase must spell the value"


def test_only_the_value_side_of_a_comparison_counts() -> None:
    above = {
        "id": "vq_above",
        "question": "Which locations are above 90 percent capacity?",
        "sql": "SELECT location_code FROM locations WHERE 100.0 * capacity_kg / capacity_kg > 90",
    }
    # 100.0 is arithmetic on the column, not a value the question chose.
    assert _echo("Show locations above 90 percent capacity", above["sql"], above) == []
    refused = _echo("Show locations above 80 percent capacity", above["sql"], above)
    assert any("'90'" in r for r in refused)
    window = {
        "id": "vq_window",
        "question": "Count transactions in the last 30 days",
        "sql": "SELECT COUNT(*) AS n FROM transactions WHERE ts >= CURRENT_DATE - INTERVAL 30 DAY",
    }
    assert any("'30'" in r for r in _echo("Count transactions in the last 7 days", window["sql"], window))
    listed = {
        "id": "vq_in",
        "question": "List transactions of type outbound or inbound",
        "sql": "SELECT txn_id FROM transactions WHERE txn_type IN ('outbound', 'inbound')",
    }
    assert any("'inbound'" in r for r in _echo("List outbound transactions", listed["sql"], listed))


def test_value_the_model_wrote_itself_is_not_this_checks_business() -> None:
    sql = "SELECT sku, quantity_kg FROM transactions WHERE sku = 'SKU-00109'"
    assert _echo("List the sku and quantity_kg of transactions for SKU-00109", sql, LISTING) == []


# -- dropped filter rule -------------------------------------------------------
def test_dropped_valueless_filter_is_refused() -> None:
    refused = _echo("List SKUs in inventory", HAZARD["sql"], HAZARD)
    assert any("'hazardous'" in r for r in refused)
    assert _echo("Show hazardous SKUs in inventory", HAZARD["sql"], HAZARD) == []


# -- copy rule -----------------------------------------------------------------
def test_copy_for_another_field_is_refused() -> None:
    refused = _echo("How many locations do we have in inventory?", SKUS["sql"], SKUS)
    assert any("'locations'" in r and "vq_skus" in r for r in refused)
    by = _echo("How many SKUs do we have in inventory by location?", SKUS["sql"], SKUS)
    assert any("'location'" in r for r in by)
    carrier = _echo("How many delayed shipments do we have by carrier?", DELAYED["sql"], DELAYED)
    assert any("'carrier'" in r for r in carrier)


def test_copy_that_ignores_a_number_is_refused() -> None:
    refused = _echo("How many delayed shipments do we have at LOC-003?", DELAYED["sql"], DELAYED)
    assert any("'003'" in r for r in refused)


def test_paraphrase_copy_is_served() -> None:
    for asked in (
        "How many distinct SKUs are currently in our inventory?",
        "What is the SKU count in inventory?",
        "how many skus do we have in inventory",
    ):
        assert _echo(asked, SKUS["sql"], SKUS) == [], asked


def test_unparseable_sql_is_refused_when_examples_were_read() -> None:
    assert _echo("How many SKUs?", "SELEC nonsense FROM", SKUS)
    assert vqa.example_echo("How many SKUs?", "SELEC nonsense FROM", [], schema=SCHEMA) == []


# -- scope: grant and schema ---------------------------------------------------
def test_example_must_be_inside_the_grant_and_the_current_schema() -> None:
    fits = vqa.example_fits
    assert fits(LISTING["sql"], granted_tables={"transactions"}, schema=SCHEMA)
    assert not fits(LISTING["sql"], granted_tables={"inventory"}, schema=SCHEMA)
    assert fits(LISTING["sql"], granted_tables=None, schema=SCHEMA)
    assert not fits(
        "SELECT sku, legacy_bin FROM transactions", granted_tables=None, schema=SCHEMA
    ), "column no longer in the schema"
    assert not fits("SELECT sku FROM old_table", granted_tables=None, schema=SCHEMA)
    assert not fits("not sql at all", granted_tables=None, schema=SCHEMA)
    joined = (
        "WITH t AS (SELECT sku, quantity_kg FROM inventory) "
        "SELECT t.sku, SUM(t.quantity_kg) AS total_kg FROM t GROUP BY t.sku ORDER BY total_kg"
    )
    assert fits(joined, granted_tables={"inventory"}, schema=SCHEMA)


def test_current_schema_reads_the_semantic_layer() -> None:
    layout = vqa.current_schema()
    assert "transactions" in layout and "sku" in layout["transactions"]
