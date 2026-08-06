"""VQ-01 — certified L0 match includes curated synonyms (not product regex)."""

from __future__ import annotations

from CortexOS.dms import answer_engine as ae
from packs.dms.semantic import loader


def test_match_certified_hits_curated_synonym():
    loader.reload()
    ae._certified_index.cache_clear() if hasattr(ae._certified_index, "cache_clear") else None
    # Force index rebuild via match
    hit = ae.match_certified("best 5 selling SKUs by revenue")
    assert hit is not None
    assert hit.id == "cq_sales_top5_value"
    assert "LIMIT 5" in hit.sql.upper() or "limit 5" in hit.sql.lower()


def test_match_certified_primary_question_still_works():
    loader.reload()
    hit = ae.match_certified("Top 5 selling SKUs by revenue")
    assert hit is not None
    assert hit.id == "cq_sales_top5_value"


def test_match_certified_unknown_stays_none():
    loader.reload()
    assert ae.match_certified("invented nonsense question xyzzy") is None
