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

# Warehouse OUT revenue by category after DISTINCT sku, category (lot dedupe).
# Naive JOIN inventory ON sku inflates ~14.8x (ELECTRONICS 133,931,869.04).
EXPECTED_TOP3_CATEGORY_SALES = (
    ("ELECTRONICS", 8_953_922.60),
    ("CHEMICALS", 8_799_446.70),
    ("FOOD_COLD", 8_754_427.11),
)
NAIVE_JOIN_ELECTRONICS_MYR = 133_931_869.04


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
    got = [(str(row["category"]), float(row["sales_value_myr"])) for row in rows]
    assert [c for c, _ in got] == [c for c, _ in EXPECTED_TOP3_CATEGORY_SALES]
    for (cat, val), (exp_cat, exp_val) in zip(got, EXPECTED_TOP3_CATEGORY_SALES, strict=True):
        assert cat == exp_cat
        assert val == pytest.approx(exp_val, abs=0.011)
        assert cat in text
        assert str(int(exp_val)) in text.replace(",", "")
    assert "FOOD_DRY" not in {c for c, _ in got}
    blob = text.replace(",", "") + str(rows)
    assert f"{NAIVE_JOIN_ELECTRONICS_MYR:.2f}".replace(",", "") not in blob.replace(",", "")
