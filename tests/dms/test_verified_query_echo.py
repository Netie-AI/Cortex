"""VERIFIED-QUERY (#309) serve-time check and example scope, as pure functions.

The HTTP must-fails in test_verified_query_contract_ask.py prove these on the
served envelope. These pin each rule's edges. An example's own SQL answers
only the question the steward confirmed with it (normalised), inside the
grant and the current schema. Any other question that runs it is refused,
including a paraphrase: abstain beats a near miss served as the answer. The
result rule refuses SQL written differently that returns an example's rows.
"""

from __future__ import annotations

from typing import Any

from CortexOS.dms import verified_query_ask as vqa

SCHEMA = {
    "shipments": frozenset(
        {"shipment_id", "sku", "supplier_id", "destination_location_id", "quantity_kg", "status", "carrier"}
    ),
    "inventory": frozenset(
        {"sku", "category", "location_id", "quantity_kg", "reorder_level_kg", "is_hazardous"}
    ),
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
HIGHEST = {
    "id": "vq_highest",
    "question": "Which 5 SKUs have the highest quantity_kg in inventory?",
    "sql": "SELECT sku, quantity_kg FROM inventory ORDER BY quantity_kg DESC LIMIT 5",
}
RANGE = {
    "id": "vq_range",
    "question": (
        "List the sku and quantity_kg of inventory where quantity_kg is above 300 "
        "and reorder_level_kg is below 10"
    ),
    "sql": "SELECT sku, quantity_kg FROM inventory WHERE quantity_kg > 300 AND reorder_level_kg < 10",
}
COPY = "only the question the steward confirmed for it may re-serve it"


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


def test_value_spelled_in_the_question_is_served_in_new_sql() -> None:
    """The model wrote new SQL that reuses a value the question spells: served."""
    by_carrier = "SELECT carrier, COUNT(*) AS n FROM shipments WHERE status = 'DELAYED' GROUP BY carrier"
    assert _echo("How many shipments are delayed, by carrier?", by_carrier, DELAYED) == []
    outbound = (
        "SELECT sku, quantity_kg FROM transactions "
        "WHERE sku = 'SKU-00296' AND txn_type = 'outbound'"
    )
    assert _echo("Show me the sku and quantity_kg of outbound SKU-00296 transactions", outbound, LISTING) == []
    # The same questions answered by the example's SQL unchanged are copies.
    assert any(COPY in r for r in _echo("How many shipments are delayed, by carrier?", DELAYED["sql"], DELAYED))
    assert any(
        COPY in r
        for r in _echo("Show me the sku and quantity_kg of outbound SKU-00296 transactions", LISTING["sql"], LISTING)
    )


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
    wider = "SELECT location_code, capacity_kg FROM locations WHERE 100.0 * capacity_kg / capacity_kg > 90"
    assert _echo("Show locations and their capacity above 90 percent capacity", wider, above) == []
    refused = _echo("Show locations above 80 percent capacity", above["sql"], above)
    assert any("'90'" in r for r in refused)
    assert not any("'100" in r for r in refused)
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
    with_category = "SELECT sku, category FROM inventory WHERE is_hazardous = true"
    assert _echo("Show hazardous SKUs and their category in inventory", with_category, HAZARD) == []
    refused = _echo("List SKUs and their category in inventory", with_category, HAZARD)
    assert any("'hazardous'" in r for r in refused), "new SQL, but it keeps the dropped filter"


# -- copy rule -----------------------------------------------------------------
def test_copy_for_another_field_is_refused() -> None:
    refused = _echo("How many locations do we have in inventory?", SKUS["sql"], SKUS)
    assert any(COPY in r and "vq_skus" in r for r in refused)
    by = _echo("How many SKUs do we have in inventory by location?", SKUS["sql"], SKUS)
    assert any(COPY in r for r in by)
    carrier = _echo("How many delayed shipments do we have by carrier?", DELAYED["sql"], DELAYED)
    assert any(COPY in r and "vq_delayed" in r for r in carrier)


def test_copy_that_ignores_a_number_is_refused() -> None:
    refused = _echo("How many delayed shipments do we have at LOC-003?", DELAYED["sql"], DELAYED)
    assert any(COPY in r and "vq_delayed" in r for r in refused)


def test_unchanged_copy_for_any_other_question_is_refused() -> None:
    """Review #321 @ 2161c14: no word decides that a copy fits another question.

    A qualifier ("inbound"), an order word ("lowest"), a negation, reordered
    words, a flipped symbol and a plain paraphrase all abstain. A word list of
    refusal triggers would fail open for every word not on it.
    """
    cases = [
        ("List the sku and quantity_kg of inbound transactions for SKU-00296", LISTING),
        ("Which 5 SKUs have the lowest quantity_kg in inventory?", HIGHEST),
        ("How many shipments are not delayed?", DELAYED),
        (
            "List the sku and quantity_kg of inventory where quantity_kg is below 300 "
            "and reorder_level_kg is above 10",
            RANGE,
        ),
        ("How many distinct SKUs are currently in our inventory?", SKUS),
        ("What is the SKU count in inventory?", SKUS),
        ("Show hazardous SKUs in inventory", HAZARD),
        ("Show me the sku and quantity_kg for SKU-00296", LISTING),
    ]
    for asked, example in cases:
        capped = example["sql"] if " LIMIT " in example["sql"] else example["sql"] + " LIMIT 1000"
        refused = _echo(asked, capped, example)
        assert any(COPY in r and example["id"] in r for r in refused), (asked, refused)


def test_copy_with_cosmetic_changes_is_still_a_copy() -> None:
    """Renamed outputs, table aliases, qualifiers, quoting, case and row cap do not hide a copy."""
    asked = "How many shipments are not delayed?"
    for sql in (
        "SELECT COUNT(*) AS n FROM shipments WHERE status = 'DELAYED'",
        "select count(*) from shipments s where s.status = 'DELAYED' limit 5",
        'SELECT COUNT(*) AS "total" FROM "shipments" AS sh WHERE sh."status" = \'DELAYED\';',
    ):
        refused = _echo(asked, sql, DELAYED)
        assert any(COPY in r for r in refused), sql
    lowest = "Which 5 SKUs have the lowest quantity_kg in inventory?"
    renamed = "SELECT i.sku, i.quantity_kg AS kg FROM inventory i ORDER BY kg DESC LIMIT 5"
    assert any(COPY in r for r in _echo(lowest, renamed, HIGHEST))
    # A real change to the query is new SQL, not a copy.
    assert _echo(lowest, "SELECT sku, quantity_kg FROM inventory ORDER BY quantity_kg ASC LIMIT 5", HIGHEST) == []
    negated = "SELECT COUNT(*) AS n FROM shipments WHERE status <> 'DELAYED'"
    assert _echo(asked, negated, DELAYED) == []


def test_confirmed_question_re_serves_its_own_sql() -> None:
    """Case, spacing and end punctuation aside, the confirmed question re-serves."""
    for asked in (
        DELAYED["question"],
        "how many delayed shipments do we have",
        "  HOW MANY DELAYED   SHIPMENTS DO WE HAVE ?! ",
    ):
        assert _echo(asked, DELAYED["sql"] + " LIMIT 1000", DELAYED) == [], asked
    assert _echo(RANGE["question"], RANGE["sql"], RANGE) == []
    assert _echo(HIGHEST["question"], HIGHEST["sql"], HIGHEST) == []
    # Two confirmed questions with one SQL: each re-serves for its own question only.
    twin = {**SKUS, "id": "vq_twin", "question": "What is the SKU count in inventory?"}
    assert _echo(twin["question"], SKUS["sql"], SKUS, twin) == []
    assert _echo(SKUS["question"], SKUS["sql"], SKUS, twin) == []
    refused = _echo("Count SKUs in inventory", SKUS["sql"], SKUS, twin)
    assert any("vq_skus" in r for r in refused) and any("vq_twin" in r for r in refused)


def test_same_question_keeps_word_order_and_symbols() -> None:
    assert vqa.same_question("How many delayed shipments do we have?", "how many delayed shipments do we have")
    assert not vqa.same_question(
        "List inventory where quantity_kg is below 300 and reorder_level_kg is above 10",
        "List inventory where quantity_kg is above 300 and reorder_level_kg is below 10",
    )
    assert not vqa.same_question("List inventory where quantity_kg < 300", "List inventory where quantity_kg > 300")
    assert not vqa.same_question("Which SKUs have the lowest stock?", "Which SKUs have the highest stock?")
    assert not vqa.same_question("", "")
    refused = _echo("List inventory where quantity_kg < 300", "SELECT sku FROM inventory WHERE quantity_kg > 300", {
        "id": "vq_gt", "question": "List inventory where quantity_kg > 300",
        "sql": "SELECT sku FROM inventory WHERE quantity_kg > 300",
    })
    assert any(COPY in r and "vq_gt" in r for r in refused)


def test_re_serve_needs_the_grant_and_the_current_schema() -> None:
    """The confirmed question re-serves only while its example still fits."""
    assert vqa.example_echo(
        DELAYED["question"], DELAYED["sql"], [DELAYED], schema=SCHEMA, granted_tables={"shipments"}
    ) == []
    outside = vqa.example_echo(
        DELAYED["question"], DELAYED["sql"], [DELAYED], schema=SCHEMA, granted_tables={"inventory"}
    )
    assert any("vq_delayed" in r and "grant" in r for r in outside), outside
    no_status = {**SCHEMA, "shipments": SCHEMA["shipments"] - {"status"}}
    stale = vqa.example_echo(DELAYED["question"], DELAYED["sql"], [DELAYED], schema=no_status)
    assert any("vq_delayed" in r and "schema" in r for r in stale), stale
    # The value rule loses its re-serve exemption the same way.
    revenue_outside = vqa.example_echo(
        REVENUE["question"], REVENUE["sql"], [REVENUE], schema=SCHEMA, granted_tables={"inventory"}
    )
    assert any("'outbound'" in r for r in revenue_outside)


def test_unparseable_sql_is_refused_when_examples_were_read() -> None:
    assert _echo("How many SKUs?", "SELEC nonsense FROM", SKUS)
    assert vqa.example_echo("How many SKUs?", "SELEC nonsense FROM", [], schema=SCHEMA) == []


# -- result rule: an equivalent rewrite returns the example's rows -------------
ROWS = "only the question the steward confirmed for it may re-serve them"
INBOUND_Q = "List the sku and quantity_kg of inbound transactions for SKU-00296"
LISTING_ROWS = [
    {"sku": "SKU-00296", "quantity_kg": 12.5},
    {"sku": "SKU-00296", "quantity_kg": 40.0},
    {"sku": "SKU-00296", "quantity_kg": 7},
]


class _Runs:
    """Stands in for the session runner: SQL text -> rows, every call recorded."""

    def __init__(self, results: dict[str, Any]) -> None:
        self.results = results
        self.calls: list[str] = []

    def __call__(self, sql: str) -> list[dict[str, Any]]:
        self.calls.append(sql)
        found = self.results[sql]
        if isinstance(found, Exception):
            raise found
        return list(found)


def _rows_echo(question: str, sql: str, runs: _Runs, *examples: dict[str, str], **kw: Any) -> list[str]:
    return vqa.result_echo(question, sql, list(examples), run=runs, schema=SCHEMA, **kw)


def test_equivalent_rewrite_returning_the_example_rows_is_refused() -> None:
    """Review @ 4bb518b: the SQL rules cannot see these; the rows can."""
    for rewrite, rows in (
        ("SELECT sku, quantity_kg FROM transactions WHERE 'SKU-00296' = sku", LISTING_ROWS),
        ("SELECT sku, quantity_kg FROM transactions WHERE sku IN ('SKU-00296')", LISTING_ROWS),
        (
            "SELECT quantity_kg, sku FROM transactions WHERE sku = 'SKU-00296'",
            [{"quantity_kg": r["quantity_kg"], "sku": r["sku"]} for r in LISTING_ROWS],
        ),
        (
            "SELECT sku, quantity_kg FROM transactions WHERE sku = 'SKU-00296' ORDER BY quantity_kg",
            sorted(LISTING_ROWS, key=lambda r: float(r["quantity_kg"])),
        ),
    ):
        assert vqa.example_echo(INBOUND_Q, rewrite, [LISTING], schema=SCHEMA) == [], rewrite
        runs = _Runs({rewrite: rows, LISTING["sql"]: LISTING_ROWS})
        refused = _rows_echo(INBOUND_Q, rewrite, runs, LISTING)
        assert refused and all(r.startswith(vqa.ECHO_CODE) for r in refused)
        assert any(ROWS in r and "vq_list" in r for r in refused), (rewrite, refused)
        assert runs.calls == [rewrite, LISTING["sql"]]


def test_rows_compare_without_names_order_or_number_spelling() -> None:
    renamed = [{"n": 1031.0}]
    runs = _Runs({"SELECT COUNT(1) AS n FROM shipments WHERE status = 'DELAYED'": renamed,
                  DELAYED["sql"]: [{"delayed_count": 1031}]})
    refused = _rows_echo(
        "How many shipments are not delayed?",
        "SELECT COUNT(1) AS n FROM shipments WHERE status = 'DELAYED'",
        runs,
        DELAYED,
    )
    assert any(ROWS in r and "vq_delayed" in r for r in refused)
    assert vqa._rows_key([{"a": " Sku-1 ", "b": True}]) == vqa._rows_key([{"x": True, "y": "sku-1"}])
    assert vqa._rows_key([{"a": 1}, {"a": 1}]) != vqa._rows_key([{"a": 1}])


def test_other_rows_are_served() -> None:
    inbound = "SELECT sku, quantity_kg FROM transactions WHERE sku = 'SKU-00296' AND txn_type = 'inbound'"
    runs = _Runs({inbound: LISTING_ROWS[:1], LISTING["sql"]: LISTING_ROWS})
    assert _rows_echo(INBOUND_Q, inbound, runs, LISTING) == []
    assert runs.calls == [inbound, LISTING["sql"]]


def test_re_serve_is_not_compared_and_twins_share_an_answer() -> None:
    """The confirmed question may get its example's rows. Two confirmed questions may share them."""
    nothing = _Runs({})
    assert _rows_echo(DELAYED["question"], DELAYED["sql"] + " LIMIT 1000", nothing, DELAYED) == []
    rewrite = "SELECT COUNT(1) FROM shipments WHERE 'DELAYED' = status"
    assert _rows_echo(DELAYED["question"], rewrite, nothing, DELAYED) == []
    assert nothing.calls == [], "only the re-served example shares the table: nothing runs"
    twin = {**DELAYED, "id": "vq_twin", "question": "How many shipments are delayed?"}
    runs = _Runs({rewrite: [{"c": 1031}], DELAYED["sql"]: [{"delayed_count": 1031}]})
    assert _rows_echo(DELAYED["question"], rewrite, runs, DELAYED, twin) == []
    refused = _rows_echo("How many shipments are not delayed?", rewrite, runs, DELAYED, twin)
    assert any("vq_delayed" in r for r in refused) and any("vq_twin" in r for r in refused)
    # A re-serve that returns other rows does not cover the copy of another example.
    pending = "SELECT COUNT(*) AS n FROM shipments WHERE status = 'PENDING'"
    other = {"id": "vq_other", "question": DELAYED["question"], "sql": pending}
    runs = _Runs({rewrite: [{"c": 1031}], DELAYED["sql"]: [{"delayed_count": 1031}], pending: [{"n": 1767}]})
    refused = _rows_echo(DELAYED["question"], rewrite, runs, other, twin)
    assert any("vq_twin" in r for r in refused) and not any("vq_other" in r for r in refused)
    assert pending in runs.calls, "the re-serve was compared and returned other rows"


def test_result_rule_fails_closed() -> None:
    rewrite = "SELECT sku, quantity_kg FROM transactions WHERE 'SKU-00296' = sku"
    broken = _Runs({rewrite: RuntimeError("gate"), LISTING["sql"]: LISTING_ROWS})
    assert any("cannot be compared" in r for r in _rows_echo(INBOUND_Q, rewrite, broken, LISTING))
    unrun = _Runs({rewrite: LISTING_ROWS[:1], LISTING["sql"]: RuntimeError("stale")})
    assert any("could not be run" in r for r in _rows_echo(INBOUND_Q, rewrite, unrun, LISTING))
    full = [{"sku": f"SKU-{i:05d}", "quantity_kg": i} for i in range(vqa.MAX_LIMIT)]
    other_full = [{"sku": f"SKU-{i:05d}", "quantity_kg": i + 1} for i in range(vqa.MAX_LIMIT)]
    capped = _Runs({rewrite: full, LISTING["sql"]: other_full})
    assert any("row cap" in r for r in _rows_echo(INBOUND_Q, rewrite, capped, LISTING))
    # No manifest to run under: the session runner refuses, so the answer does.
    no_manifest = vqa.result_echo(
        INBOUND_Q, rewrite, [LISTING], run=vqa._session_runner(None), schema=SCHEMA
    )
    assert any("cannot be compared" in r for r in no_manifest)
    assert vqa.result_echo(INBOUND_Q, "SELEC nonsense FROM", [LISTING], run=_Runs({}), schema=SCHEMA)


def test_examples_on_other_tables_are_not_run() -> None:
    runs = _Runs({})
    sql = "SELECT sku FROM inventory WHERE quantity_kg > 300"
    assert _rows_echo("List SKUs above 300 kg", sql, runs, DELAYED, LISTING) == []
    assert runs.calls == []
    assert vqa.result_echo("q", sql, [], run=runs, schema=SCHEMA) == []


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
