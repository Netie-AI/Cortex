"""METRIC-SEMANTICS-01: declared measures and the structural check that reads them.

The DMS ontology declares each sales measure once on ``transactions``:
expression, the row filter it always carries (a declared ``txn_type`` value)
and grain, plus the ``metrics.yaml`` entries that compute it.
``CortexOS.ontology.measures.check_sql`` reads parsed SQL only: an aggregate
over a declared measure's columns must pin the filter column. The decision
never reads question text.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from bench.accuracy import load_golden
from bench.corpus import load_seeds
from bench.heldout import load_heldout
from CortexOS.ontology import measures as ms
from CortexOS.ontology import value_domain as vd
from CortexOS.ontology.registry import load_object_types

ROOT = Path(__file__).resolve().parents[2]
DMS_PACK = ROOT / "packs" / "dms"


@pytest.fixture(scope="module")
def declared() -> tuple[ms.Measure, ...]:
    return ms.load_measures(DMS_PACK)


@pytest.fixture(scope="module")
def schema() -> dict[str, dict[str, str]]:
    return ms.ontology_schema(DMS_PACK)


def _kinds(sql: str, declared: Any, schema: Any) -> list[str]:
    return sorted({v.kind for v in ms.check_sql(sql, declared, schema)})


# ── the declaration ─────────────────────────────────────────────────────────


def test_sales_measures_are_declared_once_on_transactions(declared: Any) -> None:
    by_name = {m.name: m for m in declared}
    assert sorted(by_name) == ["sales_value_myr", "sales_volume_kg"]
    value = by_name["sales_value_myr"]
    assert (value.table, value.expression) == ("transactions", "SUM(quantity_kg * unit_cost_myr)")
    assert value.columns == ("quantity_kg", "unit_cost_myr")
    assert (value.filter_column, value.filter_value, value.grain) == (
        "txn_type",
        "outbound",
        "txn_id",
    )
    volume = by_name["sales_volume_kg"]
    assert (volume.expression, volume.columns) == ("SUM(quantity_kg)", ("quantity_kg",))
    # The filter value is the VALUE-NORMALIZE declaration's canonical spelling.
    (dom,) = [d for d in vd.load_value_domains(DMS_PACK) if d.key == "transactions.txn_type"]
    for m in declared:
        assert m.domain == dom.canonical
        assert m.filter_value in dom.canonical


def test_each_referenced_metric_computes_its_measure(declared: Any, schema: Any) -> None:
    """metrics.yaml stays the SQL of record; the measure is checked against it."""
    from packs.dms.semantic import loader

    compiled = loader.validate_all(execute=False)["metrics"]
    referenced = 0
    for m in declared:
        assert m.metrics, m.name
        for mid in m.metrics:
            sql = compiled[mid]
            assert ms.check_sql(sql, declared, schema) == [], (mid, sql)
            assert m.name in ms.measures_aggregated(sql, declared, schema), (mid, sql)
            # Drop the filter and the same metric is a named violation.
            bare = sql.replace("txn_type = 'outbound' AND ", "").replace(
                "WHERE txn_type = 'outbound'", ""
            )
            assert bare != sql, mid
            assert "measure_filter_missing" in _kinds(bare, declared, schema), (mid, bare)
            referenced += 1
    assert referenced == 5


def _pack(tmp_path: Path, measure: dict[str, Any], *, values: bool = True) -> Path:
    onto = tmp_path / "ontology"
    onto.mkdir(parents=True)
    txn_type: dict[str, Any] = {"name": "txn_type", "type": "string"}
    if values:
        txn_type["values"] = [{"canonical": "outbound", "from": ["OUT"]}, {"canonical": "inbound"}]
    doc = {
        "object_types": [
            {
                "id": "transactions",
                "primary_key": "txn_id",
                "properties": [
                    {"name": "txn_id", "type": "string"},
                    txn_type,
                    {"name": "quantity_kg", "type": "number"},
                ],
                "measures": [measure],
            }
        ]
    }
    (onto / "object_types.yaml").write_text(yaml.safe_dump(doc), encoding="utf-8")
    return tmp_path


GOOD = {
    "name": "sold_kg",
    "expression": "SUM(quantity_kg)",
    "filter": {"column": "txn_type", "equals": "outbound"},
    "grain": "txn_id",
}


def test_well_formed_declaration_loads(tmp_path: Path) -> None:
    (m,) = ms.load_measures(_pack(tmp_path, GOOD))
    assert (m.name, m.columns, m.filter_value, m.domain) == (
        "sold_kg",
        ("quantity_kg",),
        "outbound",
        ("outbound", "inbound"),
    )


@pytest.mark.parametrize(
    ("change", "values"),
    [
        ({"filter": {"column": "txn_type", "equals": "OUT"}}, True),  # source spelling
        ({"filter": {"column": "txn_type", "equals": "sale"}}, True),  # undeclared value
        ({"filter": {"column": "sku", "equals": "outbound"}}, True),  # not a property
        ({}, False),  # filter column declares no value domain
        ({"grain": "row_no"}, True),
        ({"expression": "SUM(weight)"}, True),
        ({"expression": "quantity_kg"}, True),  # not an aggregate
        ({"expression": "SUM(quantity_kg) + 1"}, True),
        ({"expression": "SUM(quantity_kg); DROP TABLE t"}, True),
        ({"name": "bad name"}, True),
        ({"filter": "txn_type = 'outbound'"}, True),
    ],
)
def test_declaration_a_check_could_read_two_ways_is_refused(
    tmp_path: Path, change: dict[str, Any], values: bool
) -> None:
    with pytest.raises(ValueError):
        ms.load_measures(_pack(tmp_path, {**GOOD, **change}, values=values))


def test_registry_carries_measures_on_the_object(tmp_path: Path) -> None:
    (obj,) = load_object_types(_pack(tmp_path, GOOD))
    (decl,) = obj.measures
    assert (decl.name, decl.filter_column, decl.filter_value) == ("sold_kg", "txn_type", "outbound")


# ── the structural check ────────────────────────────────────────────────────

SUM_V = "SUM(quantity_kg * unit_cost_myr)"


@pytest.mark.parametrize(
    ("sql", "kinds"),
    [
        # pinned
        (f"SELECT {SUM_V} FROM transactions WHERE txn_type = 'outbound'", []),
        (f"SELECT {SUM_V} FROM transactions WHERE txn_type IN ('outbound')", []),
        (f"SELECT {SUM_V} FROM transactions t WHERE t.txn_type = 'outbound' AND t.sku <> 'X'", []),
        (f"SELECT sku, {SUM_V} FROM transactions WHERE 'outbound' = txn_type GROUP BY sku", []),
        (f"SELECT {SUM_V} FILTER (WHERE txn_type = 'outbound') FROM transactions", []),
        (
            "SELECT SUM(CASE WHEN txn_type = 'outbound' THEN quantity_kg * unit_cost_myr "
            "ELSE 0 END) FROM transactions",
            [],
        ),
        (
            "WITH s AS (SELECT * FROM transactions WHERE txn_type = 'outbound') "
            f"SELECT location_id, {SUM_V} FROM s GROUP BY location_id",
            [],
        ),
        (
            "WITH s AS (SELECT location_id, txn_type, quantity_kg * unit_cost_myr AS v "
            "FROM transactions) SELECT SUM(v) FROM s WHERE txn_type = 'outbound'",
            [],
        ),
        (
            "SELECT x.sku, SUM(x.quantity_kg) FROM (SELECT * FROM transactions "
            "WHERE txn_type = 'outbound') AS x GROUP BY x.sku",
            [],
        ),
        (
            "SELECT l.location_code, SUM(t.quantity_kg) FROM locations l LEFT JOIN transactions t "
            "ON t.location_id = l.location_id AND t.txn_type = 'outbound' GROUP BY 1",
            [],
        ),
        (
            "SELECT SUM(t.quantity_kg) FROM transactions t JOIN locations l "
            "ON t.location_id = l.location_id AND t.txn_type = 'outbound'",
            [],
        ),
        # another declared kind, pinned explicitly: the model chose it
        ("SELECT SUM(quantity_kg) FROM transactions WHERE txn_type = 'inbound'", []),
        (
            "SELECT SUM(quantity_kg) FROM transactions WHERE txn_type IN ('ADJUST', 'WRITE_OFF')",
            [],
        ),
        # grouped (or partitioned) by the filter column: one value per kind, never mixed
        ("SELECT txn_type, SUM(quantity_kg) FROM transactions GROUP BY txn_type", []),
        ("SELECT txn_type, SUM(quantity_kg) FROM transactions GROUP BY 1", []),
        (
            "SELECT location_id, txn_type, SUM(quantity_kg) AS kg FROM transactions "
            "GROUP BY location_id, txn_type",
            [],
        ),
        (
            "WITH s AS (SELECT txn_type, SUM(quantity_kg) AS v FROM transactions "
            "GROUP BY txn_type) SELECT v FROM s WHERE txn_type = 'outbound'",
            [],
        ),
        (
            "SELECT sku, SUM(quantity_kg) OVER (PARTITION BY txn_type) FROM transactions",
            [],
        ),
        # not a declared measure aggregate
        ("SELECT COUNT(*) FROM transactions", []),
        ("SELECT SUM(unit_cost_myr) FROM transactions", []),
        ("SELECT MAX(quantity_kg) FROM transactions", []),
        (f"SELECT category, {SUM_V} FROM inventory GROUP BY category", []),
        (
            "SELECT SUM(i.quantity_kg) FROM transactions t JOIN inventory i ON t.sku = i.sku",
            [],
        ),
        ("SELECT SUM(quantity_kg) FROM shipments", []),
        # missing
        (f"SELECT {SUM_V} FROM transactions", ["measure_filter_missing"]),
        (f"SELECT sku, {SUM_V} FROM transactions GROUP BY sku", ["measure_filter_missing"]),
        ("SELECT SUM(quantity_kg) FROM transactions", ["measure_filter_missing"]),
        ("SELECT AVG(quantity_kg) FROM transactions", ["measure_filter_missing"]),
        (
            f"SELECT {SUM_V} FROM transactions WHERE txn_type = 'outbound' OR sku = 'X'",
            ["measure_filter_missing"],
        ),
        (
            f"SELECT {SUM_V} FROM transactions WHERE LOWER(txn_type) = 'outbound'",
            ["measure_filter_missing"],
        ),
        (
            "WITH s AS (SELECT * FROM transactions) "
            f"SELECT location_id, {SUM_V} FROM s GROUP BY location_id",
            ["measure_filter_missing"],
        ),
        (
            "SELECT SUM(t.quantity_kg) FROM transactions t JOIN inventory i ON t.sku = i.sku",
            ["measure_filter_missing"],
        ),
        (
            "SELECT SUM(t.quantity_kg) FROM transactions t LEFT JOIN locations l "
            "ON t.location_id = l.location_id AND t.txn_type = 'outbound'",
            ["measure_filter_missing"],
        ),
        (
            "SELECT sku, (SELECT SUM(quantity_kg) FROM transactions) AS all_kg FROM inventory",
            ["measure_filter_missing"],
        ),
        (
            "SELECT sku, SUM(quantity_kg) OVER (PARTITION BY sku) FROM transactions",
            ["measure_filter_missing"],
        ),
        (
            "WITH s AS (SELECT txn_type, SUM(quantity_kg) AS v FROM transactions "
            "GROUP BY txn_type) SELECT SUM(v) FROM s",
            ["measure_filter_missing"],
        ),
        (
            "SELECT sku, SUM(quantity_kg) FROM transactions GROUP BY sku",
            ["measure_filter_missing"],
        ),
        # inverted
        (
            f"SELECT {SUM_V} FROM transactions WHERE txn_type <> 'outbound'",
            ["measure_filter_inverted"],
        ),
        (
            f"SELECT {SUM_V} FROM transactions WHERE txn_type != 'outbound'",
            ["measure_filter_inverted"],
        ),
        (
            f"SELECT {SUM_V} FROM transactions WHERE txn_type NOT IN ('outbound', 'inbound')",
            ["measure_filter_inverted"],
        ),
        (
            f"SELECT {SUM_V} FROM transactions WHERE NOT txn_type = 'outbound'",
            ["measure_filter_inverted"],
        ),
        # #402 / #235 review: a positive pin outside the declared canonical values sums no
        # rows on a mapped lake. It is a named violation (exact text, nothing folded).
        (f"SELECT {SUM_V} FROM transactions WHERE txn_type = 'OUT'", ["measure_filter_undeclared"]),
        (
            f"SELECT {SUM_V} FROM transactions WHERE txn_type = 'Outbound'",
            ["measure_filter_undeclared"],
        ),
        (
            f"SELECT {SUM_V} FROM transactions WHERE txn_type IN ('outbound', 'OUT')",
            ["measure_filter_undeclared"],
        ),
        (
            "SELECT SUM(CASE WHEN txn_type = 'SALE' THEN quantity_kg * unit_cost_myr "
            "ELSE 0 END) FROM transactions",
            ["measure_filter_undeclared"],
        ),
        (
            "SELECT txn_type, SUM(quantity_kg) FROM transactions "
            "WHERE txn_type IN ('OUT', 'IN') GROUP BY txn_type",
            ["measure_filter_undeclared"],
        ),
        (
            f"SELECT {SUM_V} FROM transactions t WHERE t.txn_type = 'outbound' "
            "AND t.txn_type = 'out'",
            ["measure_filter_undeclared"],
        ),
        # an undeclared value that is only excluded: the declared pin stands
        (
            f"SELECT {SUM_V} FROM transactions WHERE txn_type = 'outbound' AND txn_type <> 'OUT'",
            [],
        ),
    ],
)
def test_check_reads_parsed_sql(sql: str, kinds: list[str], declared: Any, schema: Any) -> None:
    assert _kinds(sql, declared, schema) == kinds


def test_violation_reason_names_the_measure_and_its_definition(declared: Any, schema: Any) -> None:
    (v,) = ms.check_sql(f"SELECT {SUM_V} FROM transactions", declared, schema)
    reason = v.reason()
    assert reason.startswith("measure_filter_missing:sales_value_myr:")
    assert "transactions.txn_type = 'outbound'" in reason
    assert "('outbound', 'inbound', 'ADJUST', 'WRITE_OFF')" in reason
    (i,) = ms.check_sql(
        f"SELECT {SUM_V} FROM transactions WHERE txn_type <> 'outbound'", declared, schema
    )
    assert i.reason().startswith("measure_filter_inverted:sales_value_myr:")
    assert "excludes transactions.txn_type = 'outbound'" in i.reason()


def test_undeclared_pin_reason_names_the_declared_values_not_the_literal(
    declared: Any, schema: Any
) -> None:
    (v,) = ms.check_sql(f"SELECT {SUM_V} FROM transactions WHERE txn_type = 'OUT'", declared, schema)
    reason = v.reason()
    assert reason.startswith("measure_filter_undeclared:sales_value_myr:")
    assert "('outbound', 'inbound', 'ADJUST', 'WRITE_OFF')" in reason
    assert "transactions.txn_type = 'outbound'" in reason
    assert "'OUT'" not in reason


def test_no_declared_measures_means_no_check(schema: Any) -> None:
    assert ms.check_sql(f"SELECT {SUM_V} FROM transactions", (), schema) == []


# ── positive controls: every SQL the pack and bench already hold passes ──────


def _pack_and_gold_sql() -> list[tuple[str, str]]:
    from packs.dms.semantic import loader

    out = [(f"metric:{k}", v) for k, v in loader.validate_all(execute=False)["metrics"].items()]
    doc = yaml.safe_load((DMS_PACK / "semantic" / "certified_queries.yaml").read_text("utf-8"))
    out += [(f"certified:{r['id']}", str(r["sql"])) for r in doc.get("certified") or []]
    domains = vd.load_value_domains(DMS_PACK)
    gold: list[tuple[str, str]] = []
    for g in load_golden(ROOT / "bench" / "golden" / "dms_golden_v1.yaml"):
        gold.append((f"golden:{g.id}", g.canonical_sql or ""))
    for h in load_heldout(ROOT / "bench" / "heldout" / "c7_heldout_v1.yaml"):
        gold.append((f"heldout:{h.id}", h.canonical_sql or ""))
    for s in load_seeds(ROOT / "bench" / "corpus" / "seeds_v1.yaml"):
        gold.append((f"seeds:{s.id}", s.canonical_sql or ""))
    # Gold keeps source spellings; it runs through the declared mapping (as bench does).
    out += [(k, vd.canonicalize_sql(sql, domains)) for k, sql in gold if sql]
    return out


def test_pack_and_gold_sql_pass_the_check(declared: Any, schema: Any) -> None:
    rows = _pack_and_gold_sql()
    assert len(rows) > 100
    flagged = {k: [v.reason() for v in ms.check_sql(sql, declared, schema)] for k, sql in rows}
    assert {k: v for k, v in flagged.items() if v} == {}
    sales = [k for k, sql in rows if ms.measures_aggregated(sql, declared, schema)]
    assert len(sales) >= 10, sales  # the check really ran over the sales SQL
