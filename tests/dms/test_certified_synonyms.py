"""VQ-01 — certified assets match declared synonyms; BETA value-norm on lookup."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from CortexOS.dms.answer_engine import (
    _rewrite_certified_value_tokens,
    match_certified,
)


@pytest.fixture(scope="module", autouse=True)
def ensure_db():
    from bench.accuracy import _ensure_db_loaded
    from packs.dms.semantic import values as valuedict
    from packs.dms.semantic.loader import reload

    _ensure_db_loaded()
    reload()
    valuedict.refresh()
    yield


def test_certified_synonym_hits_l0():
    cq = match_certified("SKU count in inventory")
    assert cq is not None
    assert cq.id == "cq_sku_count"

    from CortexOS.dms.answer_engine import answer

    r = answer("SKU count in inventory")
    assert r["layer"] == "certified"
    assert r["badge"] == "certified"
    assert r["route"] == "sql"
    rows = r.get("rows") or []
    assert rows, f"certified sku-count returned no rows: {r.get('answer')!r}"
    text = r.get("answer") or ""
    assert text.strip(), "certified sku-count rendered no answer text"
    assert "sku" in text.lower()
    assert any(ch.isdigit() for ch in text)
    assert "sku_count" in rows[0]
    assert int(rows[0]["sku_count"]) > 0


@pytest.mark.parametrize(
    "question",
    [
        "number of skus",
        "sku count",
        "count skus",
    ],
)
def test_sku_count_metric_synonyms_hit_l1(question: str):
    """metrics.yaml sku_count synonyms must answer, not only 'how many skus'."""
    from CortexOS.dms.answer_engine import answer, route_to_metric

    plan = route_to_metric(question)
    assert plan is not None, question
    assert plan.metric_id == "sku_count"
    r = answer(question)
    assert r["layer"] == "governed_metric"
    rows = r.get("rows") or []
    assert rows, f"{question!r} returned no rows: {r.get('answer')!r}"
    text = r.get("answer") or ""
    assert text.strip()
    assert any(ch.isdigit() for ch in text)
    n = int(rows[0]["sku_count"])
    assert n > 0
    assert str(n) in text


@pytest.mark.parametrize(
    "question,metric_id",
    [
        ("distinct skus", "sku_count"),
        ("top selling", "sales_by_value"),
        ("best sellers", "sales_by_value"),
        ("stock value by category", "stock_value_by_category"),
        ("sku count by category", "sku_count_by_category"),
        ("how many SKUs per category", "sku_count_by_category"),
        ("number of products in each category", "sku_count_by_category"),
        ("recent revenue", "revenue_windowed"),
        ("high risk suppliers", "suppliers_by_risk"),
        ("pending deliveries from high risk vendors", "high_risk_pending"),
        ("shipment cost by destination", "cost_by_destination"),
        ("freight spend per destination", "cost_by_destination"),
        ("what does shipping cost us by drop-off point", "cost_by_destination"),
        ("how much do we spend in each supplier country", "spend_by_country"),
        ("procurement spend broken down by country", "spend_by_country"),
        ("which vendors are due for an audit", "audit_overdue"),
        ("camera feed for warehouse A", "cctv_by_location"),
        ("show me everything in the chemicals category", "items_by_category"),
        ("what chemical products do we hold", "items_by_category"),
        ("rank our vendors on risk and lead time together", "supplier_ranking"),
        ("risky suppliers who still owe us shipments", "high_risk_pending"),
        ("count our inventory items", "sku_count"),
        ("how many products are we stocking", "sku_count"),
        ("warehouse A stockouts risk", "low_stock"),
        ("items with no restock in 30 days", "stale_restock"),
        ("our five biggest earners by sales value", "sales_by_value"),
        ("which SKUs bring in the most money, top 5", "sales_by_value"),
        ("how much did we sell in the past 30 days", "revenue_windowed"),
        ("what is our inventory worth per category", "stock_value_by_category"),
        ("value of stock held broken down by category", "stock_value_by_category"),
        ("show me our riskiest vendors above 0.7", "suppliers_by_risk"),
        ("mean days to deliver by country", "avg_lead_time_by_country"),
    ],
)
def test_declared_yaml_synonyms_hit_stated_metric(question: str, metric_id: str):
    """metrics.yaml phrases must select that metric, not an adjacent one."""
    from CortexOS.dms.answer_engine import answer, route_to_metric

    plan = route_to_metric(question)
    assert plan is not None, question
    assert plan.metric_id == metric_id, (question, plan.metric_id)
    r = answer(question)
    assert r["badge"] != "abstain", r.get("answer")
    assert r.get("rows"), r.get("answer")
    text = r.get("answer") or ""
    assert text.strip()


def test_null_expiry_count_does_not_compile_as_sku_count():
    """Bare 'inventory items' is sku_count; a NULL-expiry filter is not."""
    from CortexOS.dms.answer_engine import answer, route_to_metric

    q = "How many inventory items have no expiry date?"
    plan = route_to_metric(q)
    assert plan is None or plan.metric_id != "sku_count"
    r = answer(q)
    assert r["badge"] == "abstain", r.get("answer")


def test_pending_high_risk_is_not_a_bare_risk_listing():
    """Nested 'high risk suppliers' must not steal the pending-shipment metric."""
    from CortexOS.dms.answer_engine import answer, route_to_metric

    q = "pending deliveries from high risk vendors"
    plan = route_to_metric(q)
    assert plan is not None
    assert plan.metric_id == "high_risk_pending"
    r = answer(q)
    assert r["badge"] != "abstain", r.get("answer")
    rows = r.get("rows") or []
    assert rows, r.get("answer")
    text = r.get("answer") or ""
    assert text.strip()
    assert "supplier_name" in rows[0]
    assert "shipment_id" in rows[0]
    assert str(rows[0]["supplier_name"]) in text
    assert str(rows[0]["shipment_id"]) in text


def test_sku_count_per_category_is_grouped_not_scalar():
    from CortexOS.dms.answer_engine import answer, route_to_metric

    q = "how many SKUs per category"
    plan = route_to_metric(q)
    assert plan is not None
    assert plan.metric_id == "sku_count_by_category"
    r = answer(q)
    assert r["badge"] != "abstain", r.get("answer")
    rows = r.get("rows") or []
    assert rows, r.get("answer")
    text = r.get("answer") or ""
    assert text.strip()
    assert "category" in rows[0]
    assert "sku_count" in rows[0]
    assert str(rows[0]["category"]) in text
    assert str(rows[0]["sku_count"]) in text.replace(",", "")


def test_freight_spend_per_destination_is_cost_not_count():
    from CortexOS.dms.answer_engine import answer, route_to_metric

    q = "freight spend per destination"
    plan = route_to_metric(q)
    assert plan is not None
    assert plan.metric_id == "cost_by_destination"
    r = answer(q)
    assert r["badge"] != "abstain", r.get("answer")
    rows = r.get("rows") or []
    assert rows, r.get("answer")
    text = r.get("answer") or ""
    assert text.strip()
    assert "location_code" in rows[0]
    assert "total_cost_myr" in rows[0]
    assert "shipment_count" not in rows[0]
    assert str(rows[0]["location_code"]) in text
    assert str(int(float(rows[0]["total_cost_myr"]))) in text.replace(",", "")


def test_spend_by_country_paraphrase_is_grouped_spend():
    from CortexOS.dms.answer_engine import answer, route_to_metric

    q = "how much do we spend in each supplier country"
    plan = route_to_metric(q)
    assert plan is not None
    assert plan.metric_id == "spend_by_country"
    r = answer(q)
    assert r["badge"] != "abstain", r.get("answer")
    rows = r.get("rows") or []
    assert rows, r.get("answer")
    text = r.get("answer") or ""
    assert text.strip()
    assert "country" in rows[0]
    assert "total_spend_myr" in rows[0]
    assert str(rows[0]["country"]) in text
    assert str(int(float(rows[0]["total_spend_myr"]))) in text.replace(",", "")


def test_cctv_warehouse_a_returns_camera_id():
    from CortexOS.dms.answer_engine import answer, route_to_metric

    q = "camera feed for warehouse A"
    plan = route_to_metric(q)
    assert plan is not None
    assert plan.metric_id == "cctv_by_location"
    assert plan.slots.get("location") == "WH-A"
    r = answer(q)
    assert r["badge"] != "abstain", r.get("answer")
    rows = r.get("rows") or []
    assert rows, r.get("answer")
    text = r.get("answer") or ""
    assert text.strip()
    assert rows[0].get("location_code") == "WH-A"
    assert rows[0].get("cctv_camera_id")
    assert "WH-A" in text
    assert str(rows[0]["cctv_camera_id"]) in text


def test_chemicals_paraphrase_lists_chemical_skus():
    from CortexOS.dms.answer_engine import answer, route_to_metric

    q = "show me everything in the chemicals category"
    plan = route_to_metric(q)
    assert plan is not None
    assert plan.metric_id == "items_by_category"
    assert str(plan.slots.get("category")).upper() == "CHEMICALS"
    r = answer(q)
    assert r["badge"] != "abstain", r.get("answer")
    rows = r.get("rows") or []
    assert rows, r.get("answer")
    text = r.get("answer") or ""
    assert text.strip()
    assert "sku" in rows[0]
    assert str(rows[0]["sku"]) in text


def test_supplier_scorecard_paraphrase_ranks_suppliers():
    from CortexOS.dms.answer_engine import answer, route_to_metric

    q = "rank our vendors on risk and lead time together"
    plan = route_to_metric(q)
    assert plan is not None
    assert plan.metric_id == "supplier_ranking"
    r = answer(q)
    assert r["badge"] != "abstain", r.get("answer")
    rows = r.get("rows") or []
    assert rows, r.get("answer")
    text = r.get("answer") or ""
    assert text.strip()
    assert "supplier_id" in rows[0]
    assert "ranking_score" in rows[0]
    assert str(rows[0]["supplier_id"]) in text


def test_certified_sales_synonym_hits_l0():
    cq = match_certified("Top 5 SKUs by revenue")
    assert cq is not None
    assert cq.id == "cq_sales_top5_value"

    from CortexOS.dms.answer_engine import answer

    r = answer("Top 5 SKUs by revenue")
    assert r["layer"] == "certified"
    assert r["badge"] == "certified"
    assert r["route"] == "sql"
    rows = r.get("rows") or []
    assert len(rows) == 5, f"certified top-5 returned {len(rows)} rows: {r.get('answer')!r}"
    text = r.get("answer") or ""
    assert text.strip(), "certified top-5 rendered no answer text"
    for row in rows:
        assert str(row["sku"]) in text
        assert "sales_value_myr" in row


def test_rewrite_beta_to_sku_beta():
    out = _rewrite_certified_value_tokens("stock of BETA")
    assert "SKU-BETA" in out
    assert "BETA" not in out.replace("SKU-BETA", "")


def test_value_norm_lookup_hits_certified_canonical(monkeypatch):
    from CortexOS.dms import answer_engine as ae

    fake = SimpleNamespace(
        id="cq_beta",
        question="stock of SKU-BETA",
        sql="SELECT 1 AS x",
        synonyms=[],
        tables=("inventory",),
    )
    monkeypatch.setattr(
        ae, "_certified_index", lambda: {"stock of sku beta": fake}
    )
    hit = ae.match_certified("stock of BETA")
    assert hit is not None
    assert hit.id == "cq_beta"


# Canonical + curated synonyms (F32 typo `categoty` included). Not intent regex.
TOP3_CATEGORY_SALES_PHRASES = (
    "show top 3 category sales",
    "show top 3 categoty sales",
    "top 3 categoty sales",
    "top 3 category sales",
    "top 3 categories by sales value",
)

# Independent of certified YAML: map sku -> category via GROUP BY, not DISTINCT.
_ORACLE_CATEGORY_SALES = """
SELECT m.category,
       ROUND(SUM(t.quantity_kg * t.unit_cost_myr), 2) AS sales_value_myr
FROM transactions t
JOIN (
    SELECT sku, MIN(category) AS category
    FROM inventory
    GROUP BY sku
) m ON t.sku = m.sku
WHERE t.txn_type = 'OUT'
GROUP BY m.category
ORDER BY sales_value_myr DESC, m.category ASC
"""
_NAIVE_LOT_JOIN_TOP = """
SELECT i.category,
       ROUND(SUM(t.quantity_kg * t.unit_cost_myr), 2) AS sales_value_myr
FROM transactions t
JOIN inventory i ON t.sku = i.sku
WHERE t.txn_type = 'OUT'
GROUP BY i.category
ORDER BY sales_value_myr DESC, i.category ASC
LIMIT 1
"""
_OUT_REVENUE = """
SELECT ROUND(SUM(quantity_kg * unit_cost_myr), 2)
FROM transactions WHERE txn_type = 'OUT'
"""


def _category_sales_oracle():
    from CortexOS.dms.warehouse_db import DEFAULT_DB, get_connection

    con = get_connection(DEFAULT_DB, read_only=True)
    try:
        overall = float(con.execute(_OUT_REVENUE).fetchone()[0])
        all_rows = [
            (str(r[0]), float(r[1])) for r in con.execute(_ORACLE_CATEGORY_SALES).fetchall()
        ]
        naive_top = float(con.execute(_NAIVE_LOT_JOIN_TOP).fetchone()[1])
        mixed = con.execute(
            "SELECT COUNT(*) FROM ("
            "SELECT sku FROM inventory GROUP BY sku "
            "HAVING COUNT(DISTINCT category) > 1)"
        ).fetchone()[0]
    finally:
        con.close()
    return overall, all_rows, naive_top, int(mixed)


@pytest.mark.parametrize("phrase", TOP3_CATEGORY_SALES_PHRASES)
def test_match_certified_hits_top3_category_sales(phrase: str):
    cq = match_certified(phrase)
    assert cq is not None, phrase
    assert cq.id == "cq_top3_category_sales"
    compact = " ".join(cq.sql.split()).lower()
    assert "select distinct sku, category from inventory" in compact
    assert "join inventory i on t.sku = i.sku" not in compact


def test_categoty_typo_hits_certified_top3_category_sales():
    """VQ-01: curated synonym, not product-intent regex. Envelope on answer()."""
    cq = match_certified("show top 3 categoty sales")
    assert cq is not None
    assert cq.id == "cq_top3_category_sales"

    from CortexOS.dms.answer_engine import answer

    r = answer("show top 3 categoty sales")
    assert r["layer"] == "certified", r.get("answer")
    assert r["badge"] == "certified"
    assert r["route"] == "sql"
    rows = r.get("rows") or []
    assert len(rows) == 3, f"expected 3 category rows, got {len(rows)}: {r.get('answer')!r}"
    text = r.get("answer") or ""
    assert text.strip()

    overall, oracle_all, naive_top, mixed_sku = _category_sales_oracle()
    assert mixed_sku == 0, "DISTINCT sku, category is unsafe if a SKU has two categories"
    oracle_sum = round(sum(v for _, v in oracle_all), 2)
    assert oracle_sum == pytest.approx(overall, abs=0.011)
    assert naive_top > overall, (
        f"naive lot JOIN should exceed OUT revenue ({naive_top} vs {overall})"
    )
    expected = oracle_all[:3]
    got = [(str(row["category"]), float(row["sales_value_myr"])) for row in rows]
    assert [c for c, _ in got] == [c for c, _ in expected]
    blob = (text + str(rows)).replace(",", "")
    for (cat, val), (exp_cat, exp_val) in zip(got, expected, strict=True):
        assert cat == exp_cat
        assert val == pytest.approx(exp_val, abs=0.011)
        assert val < overall, f"{cat}={val} exceeds OUT total {overall} (lot fan-out)"
        assert cat in text
        assert str(int(exp_val)) in text.replace(",", "")
        assert f"{int(naive_top)}" not in blob
