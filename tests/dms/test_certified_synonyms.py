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
