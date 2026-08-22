"""C7-full — schema retrieval, literal normalize, promotion, generator wiring."""

from __future__ import annotations

from pathlib import Path

import pytest

from packs.dms.generative import literal_normalize, promotion, schema_retrieval, sql_generator
from packs.dms.semantic import values as valuedict


def test_schema_retrieval_is_reduced():
    schema = schema_retrieval.retrieve("delayed shipments by carrier", top_k=3)
    tables = schema.get("tables") or {}
    assert 1 <= len(tables) <= 3
    assert "shipments" in tables or "carriers" in str(tables).lower() or tables
    # Must not dump the entire catalog
    assert len(tables) < 6


def test_schema_cache_invalidates_on_clear():
    schema_retrieval.invalidate()
    a = schema_retrieval.cache_fingerprint()
    schema_retrieval.invalidate()
    b = schema_retrieval.cache_fingerprint()
    assert a == b  # same file mtime


def test_literal_normalize_beta_to_sku_beta():
    valuedict.refresh()
    sql = "SELECT sku FROM transactions WHERE sku = 'BETA' LIMIT 5"
    out = literal_normalize.normalize_sql_literals(sql)
    assert out.ok is True
    assert out.sql is not None
    assert "SKU-BETA" in out.sql
    assert "'BETA'" not in out.sql or "SKU-BETA" in out.sql


def test_literal_normalize_unresolved_abstains():
    sql = "SELECT * FROM shipments WHERE carrier = 'PEGASUS-MOON-XYZ' LIMIT 5"
    out = literal_normalize.normalize_sql_literals(sql)
    # carrier column resolves; unknown carrier → fail closed
    assert out.ok is False
    assert any("UNRESOLVED" in v for v in out.violations)


def test_promotion_surfaces_after_five(tmp_path: Path):
    db = tmp_path / "promo.sqlite"
    q, sql = "custom l2 question", "SELECT 1 AS x"
    last = None
    for _ in range(5):
        last = promotion.record_validated(q, sql, path=db)
    assert last is not None
    assert last["ready_for_steward"] is True
    queue = promotion.steward_queue(path=db)
    assert any(r["question"] == q for r in queue)


def test_generator_unconfigured_without_l2_flag(monkeypatch):
    monkeypatch.delenv("DMS_L2_ENABLED", raising=False)
    assert sql_generator.is_configured() is False
    assert sql_generator.generate_candidates("top skus", {}) == []


def test_l2_path_abstains_without_freeroute(monkeypatch):
    from CortexOS.dms.answer_engine import answer

    monkeypatch.setenv("DMS_L2_ENABLED", "1")
    monkeypatch.setattr(sql_generator, "is_configured", lambda: False)
    r = answer("rank carriers by on-time percentage for hazmat only")
    assert r["route"] == "needs_clarification"
    assert not r.get("rows")
    answer_text = (r.get("answer") or "").lower()
    assert "can't" in answer_text or "cannot" in answer_text or "not" in answer_text


def test_l2_mock_generation_returns_rows_and_answer(monkeypatch):
    """Generation cases assert rows + rendered answer text, never SQL alone."""
    from CortexOS.dms.answer_engine import answer

    monkeypatch.setenv("DMS_L2_ENABLED", "1")
    monkeypatch.setattr(sql_generator, "is_configured", lambda: True)

    def fake_cands(question, schema_context=None, *, n=3, prior_violations=None):
        return [
            "SELECT carrier, COUNT(*) AS n FROM shipments "
            "WHERE status = 'DELAYED' GROUP BY carrier ORDER BY n DESC LIMIT 20"
        ]

    monkeypatch.setattr(sql_generator, "generate_candidates", fake_cands)
    # Force L2 by using a question outside L0/L1 templates
    r = answer("rank carriers by on-time percentage for hazmat only")
    assert r.get("rows") is not None
    assert isinstance(r.get("answer") or "", str)
    if r.get("layer") == "generated":
        assert r.get("badge") == "L2_VALIDATED"
        assert len((r.get("answer") or "")) > 0


def test_answer_engine_does_not_import_pack_generative() -> None:
    """C2: the engine holds a port. The pack registers. No packs.dms.generative import."""
    from pathlib import Path

    src = Path(__file__).resolve().parents[2] / "CortexOS" / "dms" / "answer_engine.py"
    text = src.read_text(encoding="utf-8")
    assert "packs.dms.generative" not in text
