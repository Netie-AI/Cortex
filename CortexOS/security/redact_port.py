"""Prompt redactor port: the engine side of the PII seam (C2 boundary, GH-01).

``CortexOS`` may not import ``packs.*`` (``tests/contract/test_import_boundaries.py``).
The fuller redactor (phone, NER, token vault) lives in ``packs/dms/security``, so
the dependency is inverted the same way as :mod:`CortexOS.audit.ledger_registry`:
the engine owns the port declared here, a pack may push its implementation in
with :func:`register_redactor`, and :func:`redact_prompt_text` pulls it back out.
The arrow only ever points packs -> CortexOS.

Fail-closed rules (issue #241):

* There is always an active redactor. When no pack registered one, the
  engine default (:func:`default_redact`) runs. It covers the Singapore
  NRIC/FIN, the Malaysian MyKad (with and without dashes), email and payment
  card numbers. It deliberately does not cover phone numbers, because the
  broad phone regex would eat quantities and SKUs in analytics prompts.
* A registered redactor that raises must abort the model call. The port does
  not swallow the error; ``invoke_routed_completion`` records status=error and
  re-raises, so unredacted text never leaves the process.

The pattern table is public so ``packs/dms/security/pii.py`` can reuse it
instead of carrying a second copy of the same rule.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

# Singapore NRIC/FIN: S/T/F/G/M + 7 digits + checksum letter
NRIC = re.compile(r"\b[STFGM]\d{7}[A-Z]\b", re.IGNORECASE)

# Malaysian MyKad: YYMMDD-PB-#### (place-of-birth code 01-16, 21-59, 60-68,
# 71-72, 74-79, 82-93, 98-99). Dashes optional; both forms must be separated
# from other digits so a 13+ digit card number is not split into a MyKad.
MYKAD = re.compile(
    r"(?<![\dA-Za-z])"
    r"\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])"
    r"(?:-?)"
    r"(?:0[1-9]|1[0-6]|2[1-9]|[345]\d|6[0-8]|7[124-9]|8[2-9]|9[0-3]|9[89])"
    r"(?:-?)"
    r"\d{4}"
    r"(?![\dA-Za-z])"
)

# Email (RFC5322 simplified)
EMAIL = re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b")

# Payment card: 13-19 digits with optional separators
CREDIT_CARD = re.compile(r"\b(?:\d{4}[-\s]?){3}\d{1,4}\b|\b\d{13,19}\b")

# Order matters: an earlier kind wins on an equal-start overlap, and a longer
# match wins on the same start, so identity numbers are listed before the
# broad card pattern.
ENGINE_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("nric", NRIC),
    ("mykad", MYKAD),
    ("credit_card", CREDIT_CARD),
    ("email", EMAIL),
)


@dataclass(frozen=True, slots=True)
class RedactSpan:
    start: int
    end: int
    kind: str
    text: str


def detect_spans(
    text: str, patterns: tuple[tuple[str, re.Pattern[str]], ...] = ENGINE_PATTERNS
) -> list[RedactSpan]:
    """Return non-overlapping spans for *patterns* in *text* (earliest, then longest)."""
    spans: list[RedactSpan] = []
    for kind, pattern in patterns:
        for match in pattern.finditer(text):
            spans.append(RedactSpan(match.start(), match.end(), kind, match.group(0)))
    spans.sort(key=lambda s: (s.start, -(s.end - s.start)))
    merged: list[RedactSpan] = []
    cursor = -1
    for span in spans:
        if span.start >= cursor:
            merged.append(span)
            cursor = span.end
    return merged


def apply_spans(text: str, spans: list[RedactSpan]) -> str:
    """Replace each span with ``[REDACTED:<kind>]``; spans must be non-overlapping and sorted."""
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


def default_redact(text: str) -> str:
    """Engine default: NRIC, MyKad, email and card numbers become typed placeholders."""
    return apply_spans(text, detect_spans(text, ENGINE_PATTERNS))


@runtime_checkable
class PromptRedactor(Protocol):
    """Anything callable ``str -> str`` that strips PII before a model sees the text."""

    def __call__(self, text: str) -> str: ...


class RedactionFailed(RuntimeError):
    """The active redactor raised; the model call was aborted before any text left."""


_redactor: PromptRedactor | None = None


def register_redactor(redactor: PromptRedactor) -> None:
    """Install the active redactor. Called by the owning pack, never by the engine."""
    global _redactor
    _redactor = redactor


def clear_redactor() -> None:
    """Drop the registered redactor (pack swap / test teardown); the default takes over."""
    global _redactor
    _redactor = None


def registered_redactor() -> PromptRedactor | None:
    """The pack-registered redactor, or ``None`` when the engine default is active."""
    return _redactor


def active_redactor() -> PromptRedactor:
    """The redactor a model call will run through: registered one, else the engine default."""
    return _redactor if _redactor is not None else default_redact


def redact_prompt_text(text: str) -> str:
    """Run *text* through the active redactor.

    Fail closed: any exception from the redactor becomes :class:`RedactionFailed`
    (chained), and no partially redacted text is returned. A redactor that hands
    back something other than a string is treated the same way, because the
    caller would otherwise send an unknown object to the adapter.
    """
    redactor = active_redactor()
    try:
        out = redactor(text)
    except Exception as exc:
        raise RedactionFailed(f"prompt redactor {redactor!r} raised: {exc}") from exc
    if not isinstance(out, str):
        raise RedactionFailed(
            f"prompt redactor {redactor!r} returned {type(out).__name__}, expected str"
        )
    return out


__all__ = [
    "CREDIT_CARD",
    "EMAIL",
    "ENGINE_PATTERNS",
    "MYKAD",
    "NRIC",
    "PromptRedactor",
    "RedactSpan",
    "RedactionFailed",
    "active_redactor",
    "apply_spans",
    "clear_redactor",
    "default_redact",
    "detect_spans",
    "redact_prompt_text",
    "register_redactor",
    "registered_redactor",
]
