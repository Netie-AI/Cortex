"""Unit checks for additive/subtractive session arithmetic (FOLLOWUP-01)."""

from __future__ import annotations

from CortexOS.dms.answer_engine import _scale_factor


def test_scale_factor_recognises_add_literals() -> None:
    assert _scale_factor("add 2000") == ("add", 2000.0)
    assert _scale_factor("+ 2000") == ("add", 2000.0)
    assert _scale_factor("adding 12.5") == ("add", 12.5)


def test_scale_factor_recognises_subtract_literals() -> None:
    assert _scale_factor("minus 50") == ("sub", 50.0)
    assert _scale_factor("- 2000") == ("sub", 2000.0)
    assert _scale_factor("subtracting 3") == ("sub", 3.0)


def test_scale_factor_does_not_confuse_sum_phrasing() -> None:
    assert _scale_factor("add them together") is None
    assert _scale_factor("added up") is None
