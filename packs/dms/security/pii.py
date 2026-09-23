"""PII detection and redaction for LLM prompt choke-point (F7).

The identity, email and card patterns are owned by the engine port
(``CortexOS/security/redact_port.py``, GH-01) so the DAG choke-point and this
pack redact with the same rule. Packs may import the engine; the engine never
imports packs. This module keeps its public API (``PiiSpan``, ``detect``,
``redact_for_prompt``) and adds the broad phone pattern on top, which the
engine default deliberately leaves out.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from CortexOS.security import redact_port as _port

_NRIC = _port.NRIC
_MYKAD = _port.MYKAD
_EMAIL = _port.EMAIL
_CREDIT_CARD = _port.CREDIT_CARD
_detect_spans = _port.detect_spans
_apply_spans = _port.apply_spans

# Phone: international/local with optional country code and separators
_PHONE = re.compile(
    r"(?<!\d)(?:\+?\d{1,3}[-.\s]?)?(?:\(?\d{2,4}\)?[-.\s]?)?\d{3,4}[-.\s]?\d{3,4}(?!\d)",
)

_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("nric", _NRIC),
    ("mykad", _MYKAD),
    ("credit_card", _CREDIT_CARD),
    ("email", _EMAIL),
    ("phone", _PHONE),
)


@dataclass(frozen=True, slots=True)
class PiiSpan:
    start: int
    end: int
    kind: str
    text: str


def detect(text: str) -> list[PiiSpan]:
    """Return non-overlapping PII spans found in *text*."""
    return [
        PiiSpan(start=s.start, end=s.end, kind=s.kind, text=s.text)
        for s in _detect_spans(text, _PATTERNS)
    ]


def redact_for_prompt(text: str) -> str:
    """Replace detected PII with typed placeholders before any LLM prompt."""
    return _apply_spans(text, _detect_spans(text, _PATTERNS))
