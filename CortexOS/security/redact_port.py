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
# Every pattern is scanned at every start position (a lookahead scan), and each
# candidate is validated on its own, so a rejected greedy match never hides a
# valid value inside or after it. Overlapping valid spans are merged into one.

_DASHES: Final[str] = "‐-―−"
# Not the tail of a longer digit run and not the fraction after "<digit>.".
_NUM_LEFT: Final[str] = r"(?<!\d)(?<!\d\.)"
_NUM_RIGHT: Final[str] = r"(?!\d)"

_DATE: Final[str] = r"\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])"
# JPN place-of-birth codes: 01-16, 21-59, 60-68, 71-72, 74-79, 82-93, 98-99.
_PB: Final[str] = r"(?:0[1-9]|1[0-6]|2[1-9]|[3-5]\d|6[0-8]|7[124-9]|8[2-9]|9[0-3]|9[89])"
# Between MyKad parts: a dash (variants included, optionally space-padded,
# optionally wrapped around it), or one space. Never a dot (120115.50 is a
# figure) and never a slash (INV/240115/14/0001, dated paths).
_MK_SEP: Final[str] = r"(?:\n?[ \t]?[-" + _DASHES + r"][ \t]?\n?|[ ])"
# A MyKad glued to a word (PO240115120034, file_240115143000) or after
# "<word>-" (INV-240115-01-0001) is taken only when that word is an ID label
# (IC_850505105432.pdf, MyKad850505105432, ICNo..., KP-...): see _mykad_context_ok.
_ID_LABELS: Final[frozenset[str]] = frozenset(
    {"ic", "kp", "mykad", "nric", "icno", "nokp", "kpt", "k/p", "id", "ic_no", "no_kp", "nric_no"}
)
_LABEL_TOKEN: Final[re.Pattern[str]] = re.compile(r"([A-Za-z][A-Za-z_/]*)[-_:#=]?\Z")

# Malaysian MyKad written with separators.
MYKAD_SEPARATED: Final[re.Pattern[str]] = re.compile(
    _NUM_LEFT + _DATE + _MK_SEP + _PB + _MK_SEP + r"\d{4}" + _NUM_RIGHT
)
# Twelve plain digits.
MYKAD_PLAIN: Final[re.Pattern[str]] = re.compile(_NUM_LEFT + _DATE + _PB + r"\d{4}" + _NUM_RIGHT)
MYKAD_PATTERNS: Final[tuple[re.Pattern[str], ...]] = (MYKAD_SEPARATED, MYKAD_PLAIN)
# Kept for callers that want one pattern; it does not apply the label rule.
MYKAD: Final[re.Pattern[str]] = re.compile(
    _NUM_LEFT + _DATE + r"(?:" + _MK_SEP + r")?" + _PB + r"(?:" + _MK_SEP + r")?" + r"\d{4}" + _NUM_RIGHT
)

# Singapore NRIC/FIN: S/T/F/G/M in either case, 7 digits, checksum letter (checked
# on the uppercased value; lowercase codes such as f1234567a fail it)
# below), optional single inner space or dash. Not glued to a preceding letter
# (ITEM1234567A, SYSTEM1234567X are codes); escapes such as "\n" and "%20" are
# decoded in the view first, so they do not count as letters. A digit may come
# before it (2024S1234567D).
NRIC: Final[re.Pattern[str]] = re.compile(
    r"(?<![A-Za-z])([STFGMstfgm])[ \-]?(\d{7})[ \-]?([A-Za-z])(?![A-Za-z0-9])"
)

# RFC 5322 atext local part, Unicode letters, IDN domains.
EMAIL: Final[re.Pattern[str]] = re.compile(
    r"[\w.!#$%&'*+/=?^`{|}~\-]+@[\w\-]+(?:\.[\w\-]+)*\.[^\W\d_]{2,}"
)

# Payment card: a bounded run of 14-19 digits, or the printed group shapes
# 4-4-4-4(-1..3), 4-6-5 and 4-6-4 with one consistent separator (a space, or a
# dash optionally space-padded). Not "+" or ".": 2250+1200+1200+1250 is a sum.
# Brand, length and Luhn are checked.
_CARD_SEP: Final[str] = r"(?: ?[-" + _DASHES + r"] ?|[ ])"
CARD: Final[re.Pattern[str]] = re.compile(
    _NUM_LEFT
    + r"(?:\d{14,19}"
    + r"|\d{4}(?P<s1>" + _CARD_SEP + r")\d{4}(?P=s1)\d{4}(?P=s1)\d{4}(?:(?P=s1)\d{1,3})?"
    + r"|\d{4}(?P<s2>" + _CARD_SEP + r")\d{6}(?P=s2)\d{4,5}"
    + r"|\d{4}(?P<s3>" + _CARD_SEP + r")\d{4}(?P=s3)\d{4}(?P=s3)\d{3})"
    + _NUM_RIGHT
)

_CARD_CORE: Final[re.Pattern[str]] = re.compile(r"\d{4}(\D{1,3})\d{4}\1\d{4}\1\d{4}")

ENGINE_PATTERNS: Final[tuple[tuple[str, re.Pattern[str]], ...]] = (
    ("mykad", MYKAD_SEPARATED),
    ("mykad", MYKAD_PLAIN),
    ("nric", NRIC),
    ("email", EMAIL),
    ("credit_card", CARD),
)


# (prefix regex, allowed lengths) for the networks a card number can belong to.
_CARD_BRANDS: Final[tuple[tuple[re.Pattern[str], frozenset[int]], ...]] = (
    (re.compile(r"4"), frozenset({16, 19})),  # Visa (13-digit Visa is retired; EAN-13 looks like it)
    (re.compile(r"5[1-5]|2(?:2[2-9]|[3-6]\d|7[01]|720)"), frozenset({16})),  # Mastercard
    (re.compile(r"220[0-4]"), frozenset({16, 17, 18, 19})),  # Mir
    (re.compile(r"5018|5020|5038|5893|6304|6759|676[1-3]|50[0-9]{2}"), frozenset({16, 17, 18, 19})),  # Maestro
    (re.compile(r"3[47]"), frozenset({15})),  # Amex
    (re.compile(r"6(?:011|5|4[4-9])"), frozenset({16, 17, 18, 19})),  # Discover
    (re.compile(r"35"), frozenset({16, 17, 18, 19})),  # JCB
    (re.compile(r"62"), frozenset({16, 17, 18, 19})),  # UnionPay
    (re.compile(r"3(?:0[0-5]|[689])"), frozenset({14, 15, 16, 17, 18, 19})),  # Diners
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


_NRIC_WEIGHTS: Final[tuple[int, ...]] = (2, 7, 6, 5, 4, 3, 2)
_NRIC_TABLES: Final[dict[str, tuple[int, str]]] = {
    "S": (0, "JZIHGFEDCBA"),
    "T": (4, "JZIHGFEDCBA"),
    "F": (0, "XWUTRQPNMLK"),
    "G": (4, "XWUTRQPNMLK"),
}


def nric_ok(prefix: str, digits: str, letter: str) -> bool:
    """Checksum letter for S/T/F/G. The M series is accepted on shape alone."""
    table = _NRIC_TABLES.get(prefix)
    if table is None:
        return prefix == "M"
    offset, letters = table
    total = offset + sum(w * int(d) for w, d in zip(_NRIC_WEIGHTS, digits, strict=True))
    return letters[total % 11] == letter


# Letters NFKC leaves alone that read as Latin capitals or lowercase.
_CONFUSABLES: Final[dict[str, str]] = {
    # Cyrillic
    "А": "A", "В": "B", "Е": "E", "К": "K", "М": "M", "Н": "H",
    "О": "O", "Р": "P", "С": "C", "Т": "T", "Х": "X", "Ѕ": "S",
    "а": "a", "е": "e", "о": "o", "р": "p", "с": "c", "у": "y",
    "х": "x", "ѕ": "s",
    # Greek
    "Α": "A", "Β": "B", "Ε": "E", "Ζ": "Z", "Η": "H", "Ι": "I",
    "Κ": "K", "Μ": "M", "Ν": "N", "Ο": "O", "Ρ": "P", "Τ": "T",
    "Υ": "Y", "Χ": "X",
}
# Blank-looking letters that hide inside a value.
_FILLERS: Final[frozenset[str]] = frozenset("ᅟᅠㅤﾠ⠀")
_ENTITY: Final[re.Pattern[str]] = re.compile(r"&#(?:(\d{1,7})|[xX]([0-9a-fA-F]{1,6}));")
_PERCENT: Final[re.Pattern[str]] = re.compile(r"%([0-9A-Fa-f]{2})")
# JSON and Python string escapes: json.dumps (ensure_ascii) and repr() turn a
# newline, NBSP, fullwidth digit or zero-width character into these before any
# prompt is built, so the view reads them as the character they stand for.
_BACKSLASH: Final[re.Pattern[str]] = re.compile(
    r"\\(?:u([0-9a-fA-F]{4})|U([0-9a-fA-F]{8})|x([0-9a-fA-F]{2})|([ntr]))"
)
_SIMPLE_ESCAPES: Final[dict[str, str]] = {"n": "\n", "t": "\t", "r": "\r"}

Span = tuple[int, int]


def _decode_at(text: str, pos: int) -> tuple[str, int] | None:
    """The character an escape at ``pos`` stands for, and the escape's width."""
    ch = text[pos]
    if ch == "%":
        m = _PERCENT.match(text, pos)
        if m:
            code = int(m.group(1), 16)
            if 0x20 <= code < 0x7F or code in (0x09, 0x0A, 0x0D):
                return chr(code), 3
    elif ch == "&":
        m = _ENTITY.match(text, pos)
        if m:
            code = int(m.group(1)) if m.group(1) else int(m.group(2), 16)
            if 0x09 <= code <= 0x10FFFF and not 0xD800 <= code <= 0xDFFF:
                return chr(code), m.end() - pos
    elif ch == "\\":
        m = _BACKSLASH.match(text, pos)
        if m:
            if m.group(4):
                return _SIMPLE_ESCAPES[m.group(4)], 2
            code = int(m.group(1) or m.group(2) or m.group(3), 16)
            if code <= 0x10FFFF and not 0xD800 <= code <= 0xDFFF:
                return chr(code), m.end() - pos
    return None


def normalized_view(text: str, *, format_as_space: bool = False) -> tuple[str, list[Span]]:
    """Detection view; ``index[i]`` is the source ``(start, end)`` behind view character ``i``.

    Percent escapes, HTML numeric entities and JSON/Python string escapes are
    decoded; then, for every character, format, combining and enclosing marks
    and blank fillers are dropped, NFKC is applied, any decimal digit becomes
    its ASCII digit, and confusable letters are folded. Source text is never
    changed. With ``format_as_space`` a format character (zero-width space,
    word joiner, soft hyphen, BOM) reads as a space instead of being dropped:
    dropped it can no longer split a value, read as a space it can no longer
    glue a value to the word before it. Redaction runs on both views.
    """
    out: list[str] = []
    index: list[Span] = []
    pos = 0
    n = len(text)
    while pos < n:
        decoded = _decode_at(text, pos) if text[pos] in "%&\\" else None
        if decoded is None:
            ch, width = text[pos], 1
        else:
            ch, width = decoded
        span = (pos, pos + width)
        pos += width
        if format_as_space and unicodedata.category(ch) == "Cf":
            out.append(" ")
            index.append(span)
            continue
        if ch in _FILLERS or unicodedata.category(ch) in ("Cf", "Mn", "Me"):
            continue
        for norm in unicodedata.normalize("NFKC", ch):
            if unicodedata.category(norm) in ("Mn", "Me"):
                continue
            if unicodedata.category(norm) == "Nd":
                norm = str(unicodedata.digit(norm))
            out.append(_CONFUSABLES.get(norm, norm))
            index.append(span)
    return "".join(out), index


def scan(pattern: re.Pattern[str], view: str) -> list[tuple[int, int, re.Match[str]]]:
    """Every match of ``pattern`` starting at every position, longest first per start."""
    found: list[tuple[int, int, re.Match[str]]] = []
    pos = 0
    while pos <= len(view):
        m = pattern.search(view, pos)
        if m is None:
            break
        found.append((m.start(), m.end(), m))
        pos = m.start() + 1
    return found


_EMAIL_LOCAL: Final[re.Pattern[str]] = re.compile(r"[\w.!#$%&'*+/=?^`{|}~\-]{1,64}\Z")
_EMAIL_DOMAIN: Final[re.Pattern[str]] = re.compile(r"[\w\-]+(?:\.[\w\-]+)*\.[^\W\d_]{2,}")


def email_spans(view: str) -> list[tuple[int, int, str]]:
    """Emails anchored on each "@", with the local part (<=64) and domain (<=255) bounded.

    Linear in the text: a pattern that starts on the local part would retry a
    long run of word characters from every position (quadratic on a base64 blob).
    """
    spans: list[tuple[int, int, str]] = []
    at = view.find("@")
    while at != -1:
        local = _EMAIL_LOCAL.search(view, max(0, at - 64), at)
        domain = _EMAIL_DOMAIN.match(view, at + 1, min(len(view), at + 256))
        if local and domain:
            spans.append((local.start(), domain.end(), "email"))
        at = view.find("@", at + 1)
    return spans


def _mykad_context_ok(view: str, start: int, plain: bool) -> bool:
    """False for a MyKad shape glued to a word, or after ``<word>-``, unless the word is an ID label."""
    prefix = view[max(0, start - 24) : start]
    glued = re.search(r"[A-Za-z]-\Z", prefix) is not None
    if plain:
        glued = glued or re.search(r"[A-Za-z_]\Z", prefix) is not None
    if not glued:
        return True
    m = _LABEL_TOKEN.search(prefix)
    if m is None:
        return False
    token = m.group(1).lower().strip("_/")
    parts = [p for p in re.split(r"[_]", token) if p]
    return token in _ID_LABELS or any(p in _ID_LABELS for p in parts[-2:])


def engine_spans(view: str) -> list[tuple[int, int, str]]:
    """``(start, end, kind)`` in ``view`` for every valid engine-pattern candidate."""
    spans: list[tuple[int, int, str]] = []
    for kind, pattern in ENGINE_PATTERNS:
        if kind == "email":
            spans.extend(email_spans(view))
            continue
        for start, end, match in scan(pattern, view):
            if kind == "mykad" and not _mykad_context_ok(view, start, pattern is MYKAD_PLAIN):
                continue
            if kind == "credit_card" and not card_ok(match.group(0)):
                # A 4-4-4-4 card followed by a CVV or another group: test the core.
                core = _CARD_CORE.match(match.group(0))
                if core is None or not card_ok(core.group(0)):
                    continue
                end = start + core.end()
            if kind == "nric" and not nric_ok(match.group(1).upper(), match.group(2), match.group(3).upper()):
                continue
            spans.append((start, end, kind))
    return spans


def merge_spans(spans: list[tuple[int, int, str]]) -> list[tuple[int, int, str]]:
    """Union of overlapping spans; the kind of the longest piece names the union."""
    merged: list[tuple[int, int, str, int]] = []
    for start, end, kind in sorted(spans):
        if merged and start < merged[-1][1]:
            m_start, m_end, m_kind, m_len = merged[-1]
            if end - start > m_len:
                m_kind, m_len = kind, end - start
            merged[-1] = (m_start, max(m_end, end), m_kind, m_len)
        else:
            merged.append((start, end, kind, end - start))
    return [(s, e, k) for s, e, k, _ in merged]


def apply_spans(text: str, view: str, index: list[Span], spans: list[tuple[int, int, str]]) -> str:
    """Replace the source text behind the union of ``spans`` with ``[REDACTED:<kind>]``."""
    if not spans:
        return text
    source = merge_spans([(index[s][0], index[e - 1][1], k) for s, e, k in spans])
    parts: list[str] = []
    src = 0
    for start, end, kind in source:
        parts.append(text[src:start])
        parts.append(f"[REDACTED:{kind}]")
        src = end
    parts.append(text[src:])
    return "".join(parts)


def mykad_matches(text: str) -> list[tuple[int, int]]:
    """MyKad spans in ``text`` itself (no view), for the pack's ``detect()``."""
    return sorted(
        {
            (s, e)
            for p in MYKAD_PATTERNS
            for s, e, _ in scan(p, text)
            if _mykad_context_ok(text, s, p is MYKAD_PLAIN)
        }
    )


def redact_with(text: str, finder: Callable[[str], list[tuple[int, int, str]]]) -> str:
    """Mask what ``finder`` finds on both detection views, as one union in the source."""
    source: list[tuple[int, int, str]] = []
    for format_as_space in (False, True):
        view, index = normalized_view(text, format_as_space=format_as_space)
        source.extend((index[s][0], index[e - 1][1], k) for s, e, k in finder(view))
        if not any(unicodedata.category(ch) == "Cf" for ch in text) and "&#" not in text and "\\" not in text:
            break  # no format characters: the second view is the same as the first
    if not source:
        return text
    parts: list[str] = []
    src = 0
    for start, end, kind in merge_spans(source):
        parts.append(text[src:start])
        parts.append(f"[REDACTED:{kind}]")
        src = end
    parts.append(text[src:])
    return "".join(parts)


def default_redact(text: str) -> str:
    """The engine default redactor: MyKad, NRIC/FIN, email, payment card."""
    return redact_with(text, engine_spans)


# --- registration ------------------------------------------------------------
PACK_REDACTOR_UNAVAILABLE: Final[str] = "pack_redactor_unavailable"
REDACTOR_INEFFECTIVE: Final[str] = "redactor_ineffective"

# Synthetic values every redactor must remove before it is trusted. The NRIC
# appears twice so a redactor that stops after the first hit fails.
_CANARY_VALUES: Final[tuple[str, ...]] = (
    "S0000001I",
    "canary.241@example.invalid",
    "900101-14-5678",
    "4111 1111 1111 1111",
)
_CANARY: Final[str] = "check " + " and ".join(_CANARY_VALUES) + " then S0000001I again"

_redactor: Redactor | None = None
_redactor_pack: str | None = None
_pack_loaded = False
_pack_error = False
# id -> the redactor itself. Holding the object keeps its id from being reused
# by a new function that would then skip the canary.
_trusted: dict[int, Redactor] = {}
_load_lock = threading.RLock()  # re-entered: the pack hook calls register_redactor


def _current_pack() -> str:
    from CortexOS.config import get_config

    return str(get_config().pack)


def register_redactor(redactor: Redactor) -> None:
    """Install the active pack's redactor. Called by the owning pack, never by the engine."""
    global _redactor, _redactor_pack, _pack_loaded, _pack_error
    try:
        pack: str | None = _current_pack()
    except Exception:  # noqa: BLE001 - unknown pack; the next call reloads
        pack = None
    with _load_lock:
        _redactor = redactor
        _redactor_pack = pack
        _pack_loaded = pack is not None
        _pack_error = False
        _trusted.clear()


def clear_redactor() -> None:
    """Drop the registered redactor (pack swap / test teardown). The default stays."""
    global _redactor, _redactor_pack, _pack_loaded, _pack_error
    _redactor = None
    _redactor_pack = None
    _pack_loaded = False
    _pack_error = False
    _trusted.clear()


def registered_redactor() -> Redactor | None:
    return _redactor


def _load_active_pack(pack: str) -> None:
    """Import ``packs.<pack>.security.pii`` and call its ``register_engine_redactor``.

    Resolved by name on purpose: a static ``import packs.`` here would cross the
    C2 boundary. A pack that exists but has no ``security.pii`` keeps the engine
    default. Anything else (the pack itself missing, a masker that fails to
    import, a hook that raises) sets ``_pack_error``: the call is refused, never
    sent through a weaker redactor, and the load is retried on the next call.
    """
    global _redactor, _redactor_pack, _pack_loaded, _pack_error
    # Caller holds _load_lock.
    _redactor, _redactor_pack, _pack_loaded, _pack_error = None, pack, False, True
    _trusted.clear()
    name = f"packs.{pack}.security.pii"
    try:
        module = importlib.import_module(name)
        hook = getattr(module, "register_engine_redactor", None)
        if callable(hook):
            hook()
    except ModuleNotFoundError as exc:
        # Only the masker module (or its security package) being absent means
        # "this pack has no masker". The pack itself, or anything the masker
        # imports, being missing is a broken masker.
        if exc.name not in (name, f"packs.{pack}.security"):
            return
    except Exception:  # noqa: BLE001 - a masker that exists but breaks fails closed
        return
    _redactor_pack = pack
    _pack_error = False
    _pack_loaded = True


def active_redactor() -> Redactor:
    """The redactor for the active pack. A pack change reloads; a load failure refuses."""
    try:
        pack = _current_pack()
    except Exception:  # noqa: BLE001
        pack = None
    if pack is None:
        raise RedactionRefused(PACK_REDACTOR_UNAVAILABLE)
    with _load_lock:
        if not _pack_loaded or _redactor_pack != pack:
            _load_active_pack(pack)
        if _pack_error:
            raise RedactionRefused(PACK_REDACTOR_UNAVAILABLE)
        return _redactor if _redactor is not None else default_redact


def _call_redactor(redactor: Redactor, text: str) -> object:
    """Call ``redactor``; on any error raise a refusal that carries no trace of it.

    The raise happens outside the ``except`` block, so neither ``__cause__`` nor
    ``__context__`` holds the redactor's exception (whose text may echo the input).
    """
    failed = False
    try:
        return redactor(text)
    except Exception:  # noqa: BLE001 - fail closed
        failed = True
    if failed:
        raise RedactionRefused(REDACTOR_RAISED)
    return None  # pragma: no cover


def _canary_left(out: str) -> bool:
    """True when a canary value survives, also through case, look-alike or invisible changes."""
    view, _ = normalized_view(out)
    folded = "".join(ch for ch in view.casefold() if not ch.isspace())
    return any(v.casefold().replace(" ", "") in folded for v in _CANARY_VALUES)


def _check_trusted(redactor: Redactor) -> None:
    """Run the canary once per redactor object; one that leaves a canary value is refused."""
    if _trusted.get(id(redactor)) is redactor:
        return
    out = _call_redactor(redactor, _CANARY)
    if not isinstance(out, str) or not out.strip():
        raise RedactionRefused(REDACTOR_RETURNED_NOTHING)
    if _canary_left(out):
        raise RedactionRefused(REDACTOR_INEFFECTIVE)
    _trusted[id(redactor)] = redactor


def redact_for_model(text: str) -> str:
    """Redact ``text`` with the redactor in use, or raise :class:`RedactionRefused`."""
    if not text:
        return text
    redactor = active_redactor()
    _check_trusted(redactor)
    out = _call_redactor(redactor, text)
    if not isinstance(out, str) or (text.strip() and not out.strip()):
        raise RedactionRefused(REDACTOR_RETURNED_NOTHING)
    return out


__all__ = [
    "CARD",
    "EMAIL",
    "ENGINE_PATTERNS",
    "MYKAD",
    "MYKAD_PATTERNS",
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
    "email_spans",
    "engine_spans",
    "luhn_ok",
    "merge_spans",
    "mykad_matches",
    "nric_ok",
    "normalized_view",
    "redact_for_model",
    "redact_with",
    "scan",
    "register_redactor",
    "registered_redactor",
]
