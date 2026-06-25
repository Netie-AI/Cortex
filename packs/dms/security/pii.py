"""PII detection and redaction for LLM prompt choke-point (F7)."""

from __future__ import annotations

import re
from dataclasses import dataclass

# Singapore NRIC/FIN: S/T/F/G/M + 7 digits + checksum letter
_NRIC = re.compile(r"\b[STFGM]\d{7}[A-Z]\b", re.IGNORECASE)

# Email (RFC5322 simplified)
_EMAIL = re.compile(
    r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b",
)

# Credit card: 13–19 digits with optional separators
_CREDIT_CARD = re.compile(
    r"\b(?:\d{4}[-\s]?){3}\d{1,4}\b|\b\d{13,19}\b",
)

# Phone: international/local with optional country code and separators
_PHONE = re.compile(
    r"(?<!\d)(?:\+?\d{1,3}[-.\s]?)?(?:\(?\d{2,4}\)?[-.\s]?)?\d{3,4}[-.\s]?\d{3,4}(?!\d)",
)

_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("nric", _NRIC),
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
    spans: list[PiiSpan] = []
    for kind, pattern in _PATTERNS:
        for match in pattern.finditer(text):
            spans.append(
                PiiSpan(
                    start=match.start(),
                    end=match.end(),
                    kind=kind,
                    text=match.group(0),
                )
            )
    spans.sort(key=lambda s: (s.start, -(s.end - s.start)))
    merged: list[PiiSpan] = []
    cursor = -1
    for span in spans:
        if span.start >= cursor:
            merged.append(span)
            cursor = span.end
    return merged


def redact_for_prompt(text: str) -> str:
    """Replace detected PII with typed placeholders before any LLM prompt."""
    spans = detect(text)
    if not spans:
        return text
    parts: list[str] = []
    cursor = 0
    for span in spans:
        parts.append(text[cursor : span.start])
        parts.append(f"[REDACTED:{span.kind}]")
        cursor = span.end
    parts.append(text[cursor:])
    return "".join(parts)
