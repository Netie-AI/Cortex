"""Prompt redaction port: person data never leaves through a routed completion (#241).

``CortexOS`` may not import ``packs.*`` (C2). The DMS person-data masker lives in
``packs/dms/security/pii.py``, so the dependency is inverted the same way as
:mod:`CortexOS.security.auth_port`: the engine owns this port and a default
redactor, a pack may register its own redactor with :func:`register_redactor`,
and :func:`CortexOS.execution.executor.invoke_routed_completion` calls
:func:`redact_for_model` on the system and prompt text before any cost estimate
or adapter call. The arrow only ever points packs -> CortexOS.

Rules:

* No pack redactor registered: the engine default runs, so the default path
  always masks (Malaysian MyKad, Singapore NRIC/FIN, email, payment card).
* The redactor in use raises, or returns anything but a string for a non-empty
  input: :class:`RedactionRefused` with a named reason. The caller refuses the
  completion; the text is never sent raw.
* A measured figure is never altered (DMS #508). A match touching a decimal or
  thousands separator followed by a digit, or another digit, is not person
  data: ``1250000.50``, ``12.345.678`` and ``3,400`` pass through unchanged.
* Look-alike characters (fullwidth digits and letters) and zero-width format
  characters inside a value do not hide it: detection runs on a per-character
  NFKC view with format characters dropped, and the matching source span is
  replaced. Text outside a match is passed through byte for byte.
"""

from __future__ import annotations

import importlib
import re
import unicodedata
from collections.abc import Callable
from typing import Final

Redactor = Callable[[str], str]

REDACTOR_RAISED: Final[str] = "redactor_raised"
REDACTOR_RETURNED_NOTHING: Final[str] = "redactor_returned_nothing"


class RedactionRefused(RuntimeError):
    """The completion is refused because its text could not be redacted.

    ``reason`` is one of the named constants above. The message never carries
    the text or the redactor's own exception text.
    """

    def __init__(self, reason: str) -> None:
        super().__init__(f"prompt redaction refused: {reason}")
        self.reason = reason


# --- engine patterns ---------------------------------------------------------
# Not part of a longer number: no digit, and no "<digit>." / "<digit>," joint,
# on either side. That is what keeps a measured figure intact.
_LEFT: Final[str] = r"(?<![\d])(?<!\d[.,])"
_RIGHT: Final[str] = r"(?![\d])(?![.,]\d)"

# Malaysian MyKad: YYMMDD-PB-#### with or without dashes; month and day checked.
MYKAD: Final[re.Pattern[str]] = re.compile(
    _LEFT + r"\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])-?\d{2}-?\d{4}" + _RIGHT
)
# Singapore NRIC/FIN: S/T/F/G/M + 7 digits + checksum letter.
NRIC: Final[re.Pattern[str]] = re.compile(r"(?<![A-Za-z0-9])[STFGM]\d{7}[A-Z](?![A-Za-z0-9])", re.IGNORECASE)
EMAIL: Final[re.Pattern[str]] = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
# Payment card: 13-19 digits, single spaces or dashes allowed; Luhn checked below.
CARD: Final[re.Pattern[str]] = re.compile(_LEFT + r"\d(?:[ \-]?\d){12,18}" + _RIGHT)

ENGINE_PATTERNS: Final[tuple[tuple[str, re.Pattern[str]], ...]] = (
    ("mykad", MYKAD),
    ("nric", NRIC),
    ("email", EMAIL),
    ("credit_card", CARD),
)


def luhn_ok(digits: str) -> bool:
    nums = [int(c) for c in digits if c.isdigit()]
    if not 13 <= len(nums) <= 19:
        return False
    total = 0
    for i, n in enumerate(reversed(nums)):
        if i % 2 == 1:
            n *= 2
            if n > 9:
                n -= 9
        total += n
    return total % 10 == 0


def normalized_view(text: str) -> tuple[str, list[int]]:
    """NFKC per character, format characters dropped; ``index[i]`` is the source offset."""
    out: list[str] = []
    index: list[int] = []
    for pos, ch in enumerate(text):
        if unicodedata.category(ch) == "Cf":
            continue
        for norm in unicodedata.normalize("NFKC", ch):
            out.append(norm)
            index.append(pos)
    return "".join(out), index


def engine_spans(view: str) -> list[tuple[int, int, str]]:
    """``(start, end, kind)`` in ``view`` for the engine patterns, earliest and longest first."""
    spans: list[tuple[int, int, str]] = []
    for kind, pattern in ENGINE_PATTERNS:
        for match in pattern.finditer(view):
            if kind == "credit_card" and not luhn_ok(match.group(0)):
                continue
            spans.append((match.start(), match.end(), kind))
    return spans


def apply_spans(text: str, view: str, index: list[int], spans: list[tuple[int, int, str]]) -> str:
    """Replace the source text behind each non-overlapping view span with ``[REDACTED:<kind>]``."""
    if not spans:
        return text
    chosen: list[tuple[int, int, str]] = []
    cursor = -1
    for start, end, kind in sorted(spans, key=lambda s: (s[0], -(s[1] - s[0]))):
        if start >= cursor:
            chosen.append((start, end, kind))
            cursor = end
    parts: list[str] = []
    src = 0
    for start, end, kind in chosen:
        src_start = index[start]
        src_end = index[end - 1] + 1
        if src_start < src:
            continue
        parts.append(text[src:src_start])
        parts.append(f"[REDACTED:{kind}]")
        src = src_end
    parts.append(text[src:])
    return "".join(parts)


def default_redact(text: str) -> str:
    """The engine default redactor: MyKad, NRIC/FIN, email, payment card."""
    view, index = normalized_view(text)
    return apply_spans(text, view, index, engine_spans(view))


# --- registration ------------------------------------------------------------
_redactor: Redactor | None = None
_pack_loaded = False


def register_redactor(redactor: Redactor) -> None:
    """Install the pack's redactor. Called by the owning pack, never by the engine."""
    global _redactor
    _redactor = redactor


def clear_redactor() -> None:
    """Drop the registered redactor (pack swap / test teardown). The default stays."""
    global _redactor, _pack_loaded
    _redactor = None
    _pack_loaded = False


def registered_redactor() -> Redactor | None:
    return _redactor


def _load_active_pack() -> None:
    """Import the active pack's masker once and call its ``register_engine_redactor``.

    Resolved by name on purpose: a static ``import packs.`` here would cross the
    C2 boundary. A pack without ``security.pii`` registers nothing and the engine
    default stays in use.
    """
    global _pack_loaded
    if _pack_loaded:
        return
    _pack_loaded = True
    try:
        from CortexOS.config import get_config

        module = importlib.import_module(f"packs.{get_config().pack}.security.pii")
        hook = getattr(module, "register_engine_redactor", None)
        if callable(hook):
            hook()
    except Exception:  # noqa: BLE001 - no pack masker means the engine default
        return


def active_redactor() -> Redactor:
    if _redactor is None:
        _load_active_pack()
    return _redactor if _redactor is not None else default_redact


def redact_for_model(text: str) -> str:
    """Redact ``text`` with the redactor in use, or raise :class:`RedactionRefused`."""
    if not text:
        return text
    redactor = active_redactor()
    try:
        out = redactor(text)
    except Exception:  # noqa: BLE001 - fail closed; the error text may echo the input
        raise RedactionRefused(REDACTOR_RAISED) from None
    if not isinstance(out, str) or (text.strip() and not out.strip()):
        raise RedactionRefused(REDACTOR_RETURNED_NOTHING)
    return out


__all__ = [
    "CARD",
    "EMAIL",
    "ENGINE_PATTERNS",
    "MYKAD",
    "NRIC",
    "REDACTOR_RAISED",
    "REDACTOR_RETURNED_NOTHING",
    "RedactionRefused",
    "Redactor",
    "active_redactor",
    "apply_spans",
    "clear_redactor",
    "default_redact",
    "engine_spans",
    "luhn_ok",
    "normalized_view",
    "redact_for_model",
    "register_redactor",
    "registered_redactor",
]
