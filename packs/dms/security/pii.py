"""PII detection and redaction for LLM prompt choke-point (F7)."""

from __future__ import annotations

import re
from dataclasses import dataclass

from CortexOS.security import redact_port

# #241: the engine owns the MyKad patterns. NRIC, email, card and phone keep
# this module's own patterns so detect() and redact_for_prompt() keep their
# coverage for existing callers; redact_for_model() below uses the engine
# patterns. MyKad spans are merged with the old ones as a union, never by
# dropping an overlapping span, so no value the old code masked is left raw.
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
    """Return non-overlapping PII spans found in *text*.

    Overlapping matches are merged into one span covering their union; the
    longest match names it. Coverage is never smaller than any single match.
    """
    raw: list[tuple[int, int, str]] = []
    for kind, pattern in _PATTERNS:
        for match in pattern.finditer(text):
            raw.append((match.start(), match.end(), kind))
    raw.extend((start, end, "mykad") for start, end in redact_port.mykad_matches(text))
    return [
        PiiSpan(start=start, end=end, kind=kind, text=text[start:end])
        for start, end, kind in redact_port.merge_spans(raw)
    ]


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


# Phone forms for the model path. A Malaysian local number needs a real area
# code (01x mobile, 03-09 landline, 082-089 Sabah/Sarawak) followed by a dash
# or in parentheses, then the subscriber number with at most one inner space
# (the printed forms 012-345 6789, 03-1234 5678, (03) 1234 5678). A space
# after the area code is a table row (2024 03 1500 2300), not a phone. A second dash (03-4000-1000,
# 012-3456-7890) reads as a GL or account code and stays, as do a bare digit
# run, 0800-1700 and +1.234.567; an international number starts with "+" and
# has at least 9 digits. A figure's digits are never altered (DMS #508).
_AREA = r"(?:01\d|0[3-79]|08[2-9])"
_DASH = "-\u2010-\u2015\u2212"
_LOCAL_PHONE = re.compile(
    r"(?<!\d)(?<!\d\.)(?:\(" + _AREA + r"\) ?|" + _AREA + r" ?[" + _DASH + r"] ?)"
    r"(?:\d{3,4} ?\d{4}|\d{6}|\d{3} \d{3})(?!\d)(?![-.]\d)"
)
# Country codes of the region DMS serves, plus a few common ones; not +1, so a
# signed figure (+1 234 567 890, +125 000 000) is never a phone. A separator
# after the country code is required.
_COUNTRY = r"(?:60|65|62|63|66|84|86|852|853|886|91|44|61|81|82)"
_INTL_PHONE = re.compile(
    r"(?<!\d)(?:\+" + _COUNTRY + r"[- ]|\(\+?" + _COUNTRY + r"\) ?)(?:\d{1,4}[- ]?){2,3}\d{3,4}(?!\d)(?!\.\d)"
)
# Any number right after a phone label: bare digits, a second dash, SG 8-digit,
# 60/65 without the "+". The label is what makes these phones and not figures.
_LABELLED_PHONE = re.compile(
    r"(?i)(?:\b(?:hp|h/p|tel|telephone|phone|mobile|mob|handphone|whatsapp|contact|fax)\b\.?"
    r"\s*(?:no\.?|number)?\s*[:=#.]?\s*|wa\.me/)"
    r"(\(?\+?\d[\d ()" + _DASH + r"]{6,18}\d)"
)


def _phone_spans(view: str) -> list[tuple[int, int, str]]:
    spans: list[tuple[int, int, str]] = []
    for pattern in (_LOCAL_PHONE, _INTL_PHONE):
        for start, end, match in redact_port.scan(pattern, view):
            if sum(c.isdigit() for c in match.group(0)) >= 9:
                spans.append((start, end, "phone"))
    for match in _LABELLED_PHONE.finditer(view):
        if 8 <= sum(c.isdigit() for c in match.group(1)) <= 13:
            spans.append((match.start(1), match.end(1), "phone"))
    return spans


def redact_for_model(text: str) -> str:
    """The redactor this pack registers on the engine port for routed completions.

    The engine patterns (MyKad, NRIC/FIN, email, card) plus the phone forms
    above, on the engine's detection view; overlapping matches are masked as
    their union. Text outside a match passes through unchanged.
    """
    view, index = redact_port.normalized_view(text)
    spans = redact_port.engine_spans(view) + _phone_spans(view)
    return redact_port.apply_spans(text, view, index, spans)


def register_engine_redactor() -> None:
    """Put :func:`redact_for_model` on the engine port (packs -> CortexOS).

    Called by the port when this is the active pack. Not called at import, so a
    process whose pack is not dms keeps the engine default even if some code
    path imports this module.
    """
    redact_port.register_redactor(redact_for_model)
