"""Reversible PII masking for every outbound model call (Cortex #268 PII-MASK-FREEROUTE).

:mod:`CortexOS.security.redact_port` strips PII one way (``[REDACTED:kind]``).
A model that writes SQL needs the value back: ``WHERE ic = '<PII:IC_1>'`` must
bind to the stored IC. So the outbound choke points
(:func:`CortexOS.integrations.freeroute.complete` for OpenVault and env-direct,
:func:`CortexOS.crew.llm.chat` for litellm) mask with stable placeholders
(``<PII:IC_1>``), keep the placeholder map in this process, and restore the
reply locally before anything parses or validates it.

What is masked (earliest match wins, then longest):

* IC: Malaysian MyKad and Singapore NRIC/FIN (the redact-port patterns).
* CARD: payment card numbers (redact-port pattern).
* EMAIL.
* PHONE: Malaysian mobile (``+60``/``60``/``01x``) and E.164 (``+`` then 8-15 digits).
* ACCOUNT: any other run of 10-16 digits (bank and account numbers).
* NAME: honorific-led names (``Encik``, ``Puan``, ``Dato'``, ``Mr`` ...) and
  patronymic names (``bin``/``binti``/``a/l``/``a/p``/``s/o``/``d/o``). A bare
  name with no such cue needs a registered name detector
  (:func:`register_span_detector`, for example a pack's local NER); without
  one, :data:`NAME_RULE_ONLY` is recorded so the gap is visible, not silent.

Fail closed. Any error in detection, a registered detector, or the redact
port's second pass raises :class:`MaskingFailed`; the caller refuses the call
and sends nothing. There is no switch that turns masking off.

After the reversible pass the text also runs through
:func:`CortexOS.security.redact_port.redact_prompt_text`, so a redactor a pack
registered there still applies to model calls on these paths.

The placeholder map never leaves the process: only counts by kind are exposed
(:attr:`Masked.counts`), never the values.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any

from CortexOS.security import redact_port

REFUSED_REASON = "pii_masking_failed"
NAME_RULE_ONLY = "name rule only (no name detector registered)"

PHONE_MY = re.compile(r"(?<![\d+])(?:\+?60[- ]?|0)1\d(?:[- ]?\d){7,8}(?!\d)")
PHONE_E164 = re.compile(r"(?<![\w+])\+[1-9](?:[- ]?\d){7,14}(?!\d)")
ACCOUNT = re.compile(r"(?<!\d)\d{10,16}(?!\d)")

_HONORIFIC = (
    r"(?:Tan Sri|Puan Sri|Dato'? Sri|Datuk Seri|Dato'?|Datuk|Datin|Tun|Toh Puan|"
    r"Encik|En\.|Puan|Pn\.|Cik|Tuan|Mr\.?|Mrs\.?|Ms\.?|Mdm\.?|Madam|Dr\.?)"
)
_CAP = r"[A-Z][a-zA-Z'\-]+"
NAME_HONORIFIC = re.compile(rf"\b{_HONORIFIC}\s+{_CAP}(?:\s+{_CAP}){{0,3}}")
NAME_PATRONYMIC = re.compile(
    rf"\b{_CAP}(?:\s+{_CAP}){{0,2}}\s+(?:bin|binti|bte|a/l|a/p|s/o|d/o)\s+{_CAP}(?:\s+{_CAP}){{0,2}}"
)

# Kind label on the placeholder, and the pattern. Order matters on an
# equal-start overlap (see redact_port.detect_spans).
PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("IC", redact_port.NRIC),
    ("IC", redact_port.MYKAD),
    ("CARD", redact_port.CREDIT_CARD),
    ("EMAIL", redact_port.EMAIL),
    ("PHONE", PHONE_MY),
    ("PHONE", PHONE_E164),
    ("ACCOUNT", ACCOUNT),
    ("NAME", NAME_HONORIFIC),
    ("NAME", NAME_PATRONYMIC),
)

PLACEHOLDER = re.compile(r"<PII:([A-Z]+)_(\d+)>")

# Message keys that are protocol, not content. Everything else that is a
# string, at any depth, is masked.
_SKIP_KEYS = frozenset({"role", "tool_call_id", "id", "type", "name", "cache_control"})

SpanDetector = Callable[[str], Iterable[tuple[int, int, str]]]


class MaskingFailed(RuntimeError):
    """PII masking could not complete; the model call must not be sent."""


_detector: SpanDetector | None = None


def register_span_detector(detector: SpanDetector) -> None:
    """Install an extra detector (e.g. a pack's local NER). Returns ``(start, end, kind)``."""
    global _detector
    _detector = detector


def clear_span_detector() -> None:
    global _detector
    _detector = None


def name_coverage() -> str:
    return "registered detector + name rule" if _detector is not None else NAME_RULE_ONLY


@dataclass
class Masked:
    messages: list[dict[str, Any]]
    restore: dict[str, str] = field(default_factory=dict)
    counts: dict[str, int] = field(default_factory=dict)


class _Session:
    def __init__(self) -> None:
        self.by_value: dict[tuple[str, str], str] = {}
        self.restore: dict[str, str] = {}
        self.next: dict[str, int] = {}
        self.counts: dict[str, int] = {}

    def placeholder(self, kind: str, value: str) -> str:
        key = (kind, value)
        found = self.by_value.get(key)
        if found is None:
            n = self.next.get(kind, 0) + 1
            self.next[kind] = n
            found = f"<PII:{kind}_{n}>"
            self.by_value[key] = found
            self.restore[found] = value
        self.counts[kind] = self.counts.get(kind, 0) + 1
        return found

    def mask_text(self, text: str) -> str:
        spans = redact_port.detect_spans(text, PATTERNS)
        if _detector is not None:
            extra = [
                redact_port.RedactSpan(int(s), int(e), str(k).upper() or "NAME", text[int(s) : int(e)])
                for s, e, k in _detector(text)
                if 0 <= int(s) < int(e) <= len(text)
            ]
            spans = _merge(spans + extra)
        parts: list[str] = []
        cursor = 0
        for span in spans:
            parts.append(text[cursor : span.start])
            parts.append(self.placeholder(span.kind, span.text))
            cursor = span.end
        parts.append(text[cursor:])
        # Second pass through the engine redact port (a pack-registered redactor,
        # else the engine default). It raises RedactionFailed on error.
        return redact_port.redact_prompt_text("".join(parts))

    def walk(self, value: Any, key: str = "") -> Any:
        if isinstance(value, str):
            return value if key in _SKIP_KEYS else self.mask_text(value)
        if isinstance(value, dict):
            return {k: self.walk(v, str(k)) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [self.walk(v, key) for v in value]
        return value


def _merge(spans: list[redact_port.RedactSpan]) -> list[redact_port.RedactSpan]:
    spans = sorted(spans, key=lambda s: (s.start, -(s.end - s.start)))
    merged: list[redact_port.RedactSpan] = []
    cursor = -1
    for span in spans:
        if span.start >= cursor:
            merged.append(span)
            cursor = span.end
    return merged


def mask_messages(messages: list[dict[str, Any]]) -> Masked:
    """Mask every content string in *messages*. Raises :class:`MaskingFailed` on any error."""
    session = _Session()
    try:
        out = [session.walk(dict(m)) for m in messages]
    except Exception as exc:
        raise MaskingFailed(f"{REFUSED_REASON}: {type(exc).__name__}") from exc
    return Masked(messages=out, restore=session.restore, counts=dict(session.counts))


def restore_text(text: str, restore: dict[str, str]) -> str:
    """Put the original values back in place of this call's placeholders."""
    if not restore or not text or "<PII:" not in text:
        return text
    return PLACEHOLDER.sub(lambda m: restore.get(m.group(0), m.group(0)), text)


def restore_value(value: Any, restore: dict[str, str]) -> Any:
    """:func:`restore_text` over any JSON-like value (tool-call arguments, messages)."""
    if not restore:
        return value
    if isinstance(value, str):
        return restore_text(value, restore)
    if isinstance(value, dict):
        return {k: restore_value(v, restore) for k, v in value.items()}
    if isinstance(value, list):
        return [restore_value(v, restore) for v in value]
    return value


__all__ = [
    "ACCOUNT",
    "Masked",
    "MaskingFailed",
    "NAME_RULE_ONLY",
    "PATTERNS",
    "PHONE_E164",
    "PHONE_MY",
    "PLACEHOLDER",
    "REFUSED_REASON",
    "clear_span_detector",
    "mask_messages",
    "name_coverage",
    "register_span_detector",
    "restore_text",
    "restore_value",
]
