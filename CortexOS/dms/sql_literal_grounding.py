"""An identifier the model filters on must be one the question names.

With sample values in the prompt, a model asked about an entity the data does
not hold (``SKU-ZZZZZ`` in ``LOC-999``) can filter on one it does
(``SKU-00026``, ``LOC-001``) and serve that entity's numbers as the answer. No
schema, manifest or EXPLAIN gate sees it: the SQL is valid and granted.

This checks string literals the SQL compares a column to (``=``, ``<>``, ``IN``,
``LIKE``) that look like identifiers: letters with digits, or a run of three or
more digits, and not a date, time or period code (``201202``, ``2015/2016``). Each must be grounded in the question: every alphanumeric token of the
literal, with leading zeros dropped from digit runs, appears among the
question's tokens (``SKU 26`` grounds ``SKU-00026``). Enum values and names
without digits are not checked, so synonyms (``sold`` for ``outbound``) and
value normalization (``BETA`` for ``SKU-BETA``) pass. A violation is fed back
to the model as a retry, never served.
"""

from __future__ import annotations

import re

import sqlglot
from sqlglot import exp

VIOLATION = "LITERAL_NOT_IN_QUESTION"
_DATE = re.compile(r"^\s*\d{4}([-/]\d{1,2}([-/]\d{1,2})?)?([ tT]\d{1,2}(:\d{2}){0,2}(\.\d+)?)?\s*$")
_TIME = re.compile(r"^\s*\d{1,2}:\d{2}(:\d{2})?\s*$")
# Period encodings a question names in words: 201202, 20120218, 2015/2016.
_PERIOD = re.compile(r"^(19|20)\d{2}((0[1-9]|1[0-2])(\d{2})?|[/-](19|20)?\d{2})$")
_TOKEN = re.compile(r"[a-z]+|\d+")
_COMPARE = (exp.EQ, exp.NEQ, exp.In, exp.Like, exp.ILike)


def _tokens(text: str) -> list[str]:
    return [t.lstrip("0") or "0" if t.isdigit() else t for t in _TOKEN.findall(text.lower())]


def _identifier_like(value: str) -> bool:
    """A code (letters and digits) or a long number; never a date, time or tiny number."""
    raw = value.strip().strip("%_-/ ")
    if _DATE.match(raw) or _TIME.match(raw) or _PERIOD.match(raw) or not re.search(r"\d", raw):
        return False
    return bool(re.search(r"[A-Za-z]", raw)) or bool(re.search(r"\d{3,}", raw))


def _compared_literals(tree: exp.Expression) -> list[str]:
    out: list[str] = []
    for node in tree.find_all(*_COMPARE):
        if not any(isinstance(side, exp.Column) for side in node.iter_expressions()):
            continue
        for lit in node.find_all(exp.Literal):
            if lit.is_string and lit.this not in out:
                out.append(lit.this)
    return out


def ungrounded_literals(question: str, sql: str, *, dialect: str = "duckdb") -> list[str]:
    """``LITERAL_NOT_IN_QUESTION:<literal>`` per identifier the question does not name."""
    try:
        tree = sqlglot.parse_one(sql, read=dialect)
    except Exception:  # noqa: BLE001 - an unparseable query is the SQL gate's to refuse
        return []
    asked = set(_tokens(question))
    out = []
    for literal in _compared_literals(tree):
        if not _identifier_like(literal):
            continue
        needed = _tokens(literal)
        if needed and not set(needed) <= asked:
            out.append(f"{VIOLATION}:{literal}")
    return out
