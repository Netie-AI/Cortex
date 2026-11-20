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
* A measured figure is never altered (DMS #508). The patterns are structural,
  not positional: a MyKad needs a real birth date and place-of-birth code, a
  card a real brand prefix and length plus Luhn, and no match may continue a
  longer digit run or be the fraction after ``<digit>.``. ``1250000.50``,
  ``12.345.678``, ``120115000000`` (place code 00) and a millisecond timestamp
  pass through unchanged. An ID next to a comma (a CSV cell) or rendered as a
  float (``850505105432.0``) is still an ID and is masked.
* Look-alike characters (fullwidth digits and letters) and zero-width format
  characters inside a value do not hide it: detection runs on a per-character
  NFKC view with format characters dropped, and the matching source span is
  replaced. Text outside a match is passed through byte for byte.
"""

from __future__ import annotations

import importlib
import re
import threading
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
# Not the tail of a longer digit run and not the fraction after "<digit>.";
# not followed by another digit.
_LEFT: Final[str] = r"(?<![\d])(?<!\d\.)(?<![A-Za-z]-)"
_RIGHT: Final[str] = r"(?!\d)"
# One separator between MyKad parts: dash, dash variants, space or dot.
_SEP: Final[str] = r"[-\u2010-\u2015\u2212 .]?"
# JPN place-of-birth codes: 01-16, 21-59, 60-68, 71-72, 74-79, 82-93, 98-99.
_PB: Final[str] = r"(?:0[1-9]|1[0-6]|2[1-9]|[3-5]\d|6[0-8]|7[124-9]|8[2-9]|9[0-3]|9[89])"

# Malaysian MyKad: YYMMDD-PB-####, with or without separators.
MYKAD: Final[re.Pattern[str]] = re.compile(
    _LEFT + r"\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])" + _SEP + _PB + _SEP + r"\d{4}" + _RIGHT
)
# Singapore NRIC/FIN: S/T/F/G/M + 7 digits + checksum letter, inner spaces allowed.
NRIC: Final[re.Pattern[str]] = re.compile(
    r"(?<![A-Za-z0-9])[STFGM][ \-]?\d{7}[ \-]?[A-Z](?![A-Za-z0-9])", re.IGNORECASE
)
# Unicode local parts and IDN domains included (\w is Unicode on str patterns).
EMAIL: Final[re.Pattern[str]] = re.compile(r"[\w.%+'\-]+@[\w\-]+(?:\.[\w\-]+)*\.[^\W\d_]{2,}")
# Payment card: 13-19 digits, up to two spaces or dashes between digits;
# brand prefix, length and Luhn checked below.
CARD: Final[re.Pattern[str]] = re.compile(_LEFT + r"\d(?:[ \t\-\u2010-\u2015]{0,2}\d){12,18}" + _RIGHT)

ENGINE_PATTERNS: Final[tuple[tuple[str, re.Pattern[str]], ...]] = (
    ("mykad", MYKAD),
    ("nric", NRIC),
    ("email", EMAIL),
    ("credit_card", CARD),
)


# (prefix regex, allowed lengths) for the networks a card number can belong to.
_CARD_BRANDS: Final[tuple[tuple[re.Pattern[str], frozenset[int]], ...]] = (
    (re.compile(r"4"), frozenset({13, 16, 19})),  # Visa
    (re.compile(r"5[1-5]|2(?:2[2-9]|[3-6]\d|7[01]|720)"), frozenset({16})),  # Mastercard
    (re.compile(r"3[47]"), frozenset({15})),  # Amex
    (re.compile(r"6(?:011|5|4[4-9])"), frozenset({16, 17, 18, 19})),  # Discover
    (re.compile(r"35"), frozenset({16, 17, 18, 19})),  # JCB
    (re.compile(r"62"), frozenset({16, 17, 18, 19})),  # UnionPay
    (re.compile(r"3(?:0[0-5]|[68])"), frozenset({14, 15, 16, 17, 18, 19})),  # Diners
)


def card_ok(value: str) -> bool:
    digits = "".join(c for c in value if c.isdigit())
    if not luhn_ok(digits):
        return False
    return any(p.match(digits) and len(digits) in lengths for p, lengths in _CARD_BRANDS)


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


# Letters NFKC leaves alone that read as the NRIC prefix letters.
_CONFUSABLES: Final[dict[str, str]] = {
    "\u0405": "S", "\u0455": "s", "\u0422": "T", "\u0442": "t", "\u041c": "M", "\u043c": "m",
    "\u03a4": "T", "\u03c4": "t", "\u039c": "M", "\u0393": "G",
}


def normalized_view(text: str) -> tuple[str, list[int]]:
    """Detection view; ``index[i]`` is the source offset of view character ``i``.

    Per character: format characters dropped, NFKC, any decimal digit as its
    ASCII digit, and a few confusable letters folded.
    """
    out: list[str] = []
    index: list[int] = []
    for pos, ch in enumerate(text):
        if unicodedata.category(ch) == "Cf":
            continue
        for norm in unicodedata.normalize("NFKC", ch):
            if unicodedata.category(norm) == "Nd":
                norm = str(unicodedata.digit(norm))
            out.append(_CONFUSABLES.get(norm, norm))
            index.append(pos)
    return "".join(out), index


def engine_spans(view: str) -> list[tuple[int, int, str]]:
    """``(start, end, kind)`` in ``view`` for the engine patterns, earliest and longest first."""
    spans: list[tuple[int, int, str]] = []
    for kind, pattern in ENGINE_PATTERNS:
        for match in pattern.finditer(view):
            if kind == "credit_card" and not card_ok(match.group(0)):
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
PACK_REDACTOR_UNAVAILABLE: Final[str] = "pack_redactor_unavailable"
REDACTOR_INEFFECTIVE: Final[str] = "redactor_ineffective"

# Synthetic values every redactor must remove before it is trusted.
_CANARY_VALUES: Final[tuple[str, ...]] = ("S0000001I", "canary.241@example.invalid")
_CANARY: Final[str] = "check " + " and ".join(_CANARY_VALUES)

_redactor: Redactor | None = None
_pack_loaded = False
_pack_error = False
_trusted: set[int] = set()
_load_lock = threading.Lock()


def register_redactor(redactor: Redactor) -> None:
    """Install the pack's redactor. Called by the owning pack, never by the engine."""
    global _redactor
    _redactor = redactor


def clear_redactor() -> None:
    """Drop the registered redactor (pack swap / test teardown). The default stays."""
    global _redactor, _pack_loaded, _pack_error
    _redactor = None
    _pack_loaded = False
    _pack_error = False


def registered_redactor() -> Redactor | None:
    return _redactor


def _load_active_pack() -> None:
    """Import the active pack's masker and call its ``register_engine_redactor``.

    Resolved by name on purpose: a static ``import packs.`` here would cross the
    C2 boundary. A pack with no ``security.pii`` module keeps the engine default.
    A pack whose masker exists but fails to load sets ``_pack_error``: the call
    is refused, never sent through a weaker redactor, and the load is retried
    on the next call.
    """
    global _pack_loaded, _pack_error
    with _load_lock:
        if _pack_loaded:
            return
        name = ""
        try:
            from CortexOS.config import get_config

            name = f"packs.{get_config().pack}.security.pii"
            module = importlib.import_module(name)
            hook = getattr(module, "register_engine_redactor", None)
            if callable(hook):
                hook()
        except ModuleNotFoundError as exc:
            missing = exc.name or ""
            if not name or not name.startswith(missing + ".") and missing != name:
                _pack_error = True
                return
        except Exception:  # noqa: BLE001 - a masker that exists but breaks fails closed
            _pack_error = True
            return
        _pack_error = False
        _pack_loaded = True


def active_redactor() -> Redactor:
    if _redactor is None:
        _load_active_pack()
        if _pack_error:
            raise RedactionRefused(PACK_REDACTOR_UNAVAILABLE)
    return _redactor if _redactor is not None else default_redact


def _check_trusted(redactor: Redactor) -> None:
    """Run the canary once per redactor; one that leaves a canary value is refused."""
    if id(redactor) in _trusted:
        return
    try:
        out = redactor(_CANARY)
    except Exception:  # noqa: BLE001
        raise RedactionRefused(REDACTOR_RAISED) from None
    if not isinstance(out, str) or not out.strip():
        raise RedactionRefused(REDACTOR_RETURNED_NOTHING)
    if any(v in out for v in _CANARY_VALUES):
        raise RedactionRefused(REDACTOR_INEFFECTIVE)
    _trusted.add(id(redactor))


def redact_for_model(text: str) -> str:
    """Redact ``text`` with the redactor in use, or raise :class:`RedactionRefused`."""
    if not text:
        return text
    redactor = active_redactor()
    _check_trusted(redactor)
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
    "PACK_REDACTOR_UNAVAILABLE",
    "REDACTOR_INEFFECTIVE",
    "REDACTOR_RAISED",
    "REDACTOR_RETURNED_NOTHING",
    "RedactionRefused",
    "Redactor",
    "active_redactor",
    "apply_spans",
    "card_ok",
    "clear_redactor",
    "default_redact",
    "engine_spans",
    "luhn_ok",
    "normalized_view",
    "redact_for_model",
    "register_redactor",
    "registered_redactor",
]
