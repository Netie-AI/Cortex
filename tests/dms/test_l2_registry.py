"""C2 inversion — L2 generation reaches the pack through a port, not an import."""

from __future__ import annotations

from pathlib import Path

from CortexOS.dms.l2_registry import resolve_l2


def test_dms_pack_registers_l2_modules() -> None:
    import packs.dms

    packs.dms.register_engine_seams()
    port = resolve_l2()
    assert callable(port.sql_generator.is_configured)
    assert callable(port.sql_generator.generate_candidates)
    assert callable(port.schema_retrieval.retrieve)
    assert callable(port.promotion.record_validated)


def test_answer_engine_has_no_static_pack_generative_import() -> None:
    src = (Path(__file__).resolve().parents[2] / "CortexOS" / "dms" / "answer_engine.py")
    text = src.read_text(encoding="utf-8")
    assert "from packs.dms.generative" not in text
    assert "import packs.dms.generative" not in text
