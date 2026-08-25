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
    assert r.get("rows") is not None
    text = (r.get("answer") or "").lower()
    assert "sku" in text or r["rows"]


def test_certified_sales_synonym_hits_l0():
    cq = match_certified("Top 5 SKUs by revenue")
    assert cq is not None
    assert cq.id == "cq_sales_top5_value"


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
