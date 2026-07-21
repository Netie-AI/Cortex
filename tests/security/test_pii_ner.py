"""Layered detector: regex floor always works; optional layers degrade gracefully."""

from __future__ import annotations

from packs.dms.security.pii import PiiSpan
from packs.dms.security.pii_ner import LayeredDetector, default_detector
from packs.dms.security.token_vault import TokenVault

SAMPLE = "Contact john.doe@acme.com or call +65 8123 4567."


def test_regex_floor_offline() -> None:
    det = default_detector()  # regex only
    assert det.layers == ("regex",)
    spans = det.detect(SAMPLE)
    kinds = {s.kind for s in spans}
    assert "email" in kinds
    assert all(isinstance(s, PiiSpan) for s in spans)


def test_layered_never_below_regex() -> None:
    # even requesting NER, if presidio is absent the floor is unchanged
    det = LayeredDetector(use_ner=True)
    layered = {(s.start, s.end) for s in det.detect(SAMPLE)}
    from packs.dms.security.pii import detect as regex_detect
    floor = {(s.start, s.end) for s in regex_detect(SAMPLE)}
    assert floor <= layered  # never fails open to less than regex


def test_layered_feeds_token_vault() -> None:
    det = default_detector()
    vault = TokenVault(salt=b"fixed-test-salt.")
    res = vault.mask(SAMPLE, detector=det.detect)
    assert "john.doe@acme.com" not in res.masked
    assert "john.doe@acme.com" in vault.unmask(res.masked)
