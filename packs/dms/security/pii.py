"""PII detection and redaction for LLM prompt choke-point (F7)."""

from __future__ import annotations

import re
from dataclasses import dataclass

from CortexOS.security import redact_port

# #241: the engine owns the MyKad pattern. NRIC and email keep this module's
# own patterns so detect() and redact_for_prompt() behave as before for their
# existing callers; redact_for_model() below uses the engine patterns.
_MYKAD = redact_port.MYKAD

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
    ("mykad", _MYKAD),
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


_FIGURE_JOINT_LEFT = re.compile(r"\d[.,\-\s]?$")
_FIGURE_JOINT_RIGHT = re.compile(r"^[.,\-\s]?\d")
# 0-led local number: a 0 + 1-3 digit area group, a separator, then 6-8 digits.
_LOCAL_PHONE = re.compile(r"0\d{1,3}[-\s]\d{3,4}[-\s]?\d{3,4}")


def _phone_is_unambiguous(value: str) -> bool:
    """A phone form no measured figure takes: ``+60 12-345 6789`` or ``012-345 6789``.

    A bare digit run or ``1200-3400`` can be a quantity or a range, and a figure's
    digits are never altered (DMS #508), so those stay.
    """
    digits = sum(c.isdigit() for c in value)
    if digits < 9:
        return False  # 0800-1700, 0-150000 and +1 234 567 are ranges or figures
    if value.startswith("+"):
        return True
    return _LOCAL_PHONE.fullmatch(value) is not None


def redact_for_model(text: str) -> str:
    """The redactor this pack registers on the engine port for routed completions.

    The engine patterns (MyKad, NRIC/FIN, email, Luhn-valid card), plus phone
    numbers in a form no measured figure takes. Look-alike and zero-width
    characters are seen through; text outside a match passes through unchanged.
    """
    view, index = redact_port.normalized_view(text)
    spans = redact_port.engine_spans(view)
    for match in _PHONE.finditer(view):
        value = match.group(0)
        touches_figure = _FIGURE_JOINT_LEFT.search(view[max(0, match.start() - 2) : match.start()]) or (
            _FIGURE_JOINT_RIGHT.search(view[match.end() : match.end() + 2])
        )
        if not touches_figure and _phone_is_unambiguous(value):
            spans.append((match.start(), match.end(), "phone"))
    return redact_port.apply_spans(text, view, index, spans)


def register_engine_redactor() -> None:
    """Put :func:`redact_for_model` on the engine port (packs -> CortexOS).

    Called by the port when this is the active pack. Not called at import, so a
    process whose pack is not dms keeps the engine default even if some code
    path imports this module.
    """
    redact_port.register_redactor(redact_for_model)
