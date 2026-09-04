"""Constructor skin seeds. P1 parked. Cortex is the engine."""

from pathlib import Path

SKIN = Path(__file__).resolve().parents[2] / "CortexOS" / "constructor_skin"


def test_skin_has_constructor_seed_buttons() -> None:
    html = (SKIN / "index.html").read_text(encoding="utf-8")
    assert 'data-seed="define"' in html
    assert 'data-seed="govern"' in html
    assert 'data-seed="insights"' in html
    assert 'data-seed="understand"' in html
    assert "Define data" in html
    assert "Govern agents" in html
    assert "Client company" in html
    engine = (SKIN / "engine.js").read_text(encoding="utf-8")
    assert "govern agents?" in engine
    assert "define data" in engine
    assert "generateLocal" in engine
    assert "constructor/catalog" in engine
    assert "understand this company" in engine or "clientish" in engine
    assert "seed (warehouse|venue|suspect|foundry|define|govern|insights|understand)" in engine
    assert "loadCompilePath" in engine
    app = (SKIN / "app.js").read_text(encoding="utf-8")
    assert "define data for inventory" in app
    assert "govern agents on inventory" in app
    assert "business insights on inventory" in app
    assert "understand this company" in app
    assert 'label: "Compile"' in app
    assert "loadCompilePath" in app
