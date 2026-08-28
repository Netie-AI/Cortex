"""Normalize string literals in generated SQL against column encodings.

G4-class bug: ``BETA`` must become ``SKU-BETA`` before the predicate ships.
Unresolvable literals abstain with candidates listed — never emit a filter
that matches nothing while stamping success.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from packs.dms.semantic import values as valuedict

# Columns we know how to resolve (same registry as values.py).
_RESOLVE_COLUMNS = frozenset(valuedict.VALUE_COLUMNS.keys())

# Rough column hint from surrounding SQL text.
_COL_HINT = re.compile(
    r"\b(" + "|".join(re.escape(c) for c in sorted(_RESOLVE_COLUMNS, key=len, reverse=True)) + r")\b",
    re.I,
)
_STRING_LIT = re.compile(r"'((?:''|[^'])*)'")


@dataclass(slots=True)
class NormalizeResult:
    ok: bool
    sql: str | None
    violations: list[str] = field(default_factory=list)
    replacements: list[tuple[str, str, str]] = field(default_factory=list)  # col, old, new


class LiteralUnresolved(Exception):
    def __init__(self, message: str, *, candidates: list[str] | None = None) -> None:
        super().__init__(message)
        self.candidates = list(candidates or [])


def _guess_column(sql: str, lit_start: int) -> str | None:
    window = sql[max(0, lit_start - 80) : lit_start]
    hits = list(_COL_HINT.finditer(window))
    if not hits:
        return None
    return hits[-1].group(1).lower()


def normalize_sql_literals(sql: str) -> NormalizeResult:
    """Rewrite entity literals using valuedict.resolve. Fail closed on ambiguity."""
    if not sql or not sql.strip():
        return NormalizeResult(ok=False, sql=None, violations=["EMPTY_SQL"])

    out_parts: list[str] = []
    last = 0
    replacements: list[tuple[str, str, str]] = []
    violations: list[str] = []

    for m in _STRING_LIT.finditer(sql):
        raw = m.group(1).replace("''", "'")
        col = _guess_column(sql, m.start())
        out_parts.append(sql[last : m.start()])
        if col is None or col not in _RESOLVE_COLUMNS:
            # Keep non-entity literals (dates, free text) unchanged.
            out_parts.append(m.group(0))
            last = m.end()
            continue
        res = valuedict.resolve(raw, col)
        if not res.ok or not res.value:
            violations.append(
                f"UNRESOLVED_LITERAL:{col}={raw!r} candidates={res.candidates[:5]}"
            )
            out_parts.append(m.group(0))
            last = m.end()
            continue
        if res.value != raw:
            replacements.append((col, raw, res.value))
        escaped = res.value.replace("'", "''")
        out_parts.append(f"'{escaped}'")
        last = m.end()

    out_parts.append(sql[last:])
    if violations:
        return NormalizeResult(ok=False, sql=None, violations=violations, replacements=replacements)
    return NormalizeResult(ok=True, sql="".join(out_parts), replacements=replacements)


__all__ = ["LiteralUnresolved", "NormalizeResult", "normalize_sql_literals"]
