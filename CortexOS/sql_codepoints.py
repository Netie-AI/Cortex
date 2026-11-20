"""Characters the SQL gates and DuckDB split differently. Pure string code.

Every SQL gate here reads a statement with sqlglot and DuckDB runs it with its
own parser. A gate is only as good as the agreement between the two on where
one token ends. sqlglot 30 keeps some invisible format characters (U+200B zero
width space, U+2060 word joiner, U+FEFF byte order mark) inside an identifier
and writes them back unquoted; DuckDB 1.5 treats the same characters as
whitespace. ``FROM t a<U+200B>CROSS<U+200B>JOIN<U+200B>other`` is one table with
an odd alias to every gate and a cross join to DuckDB, so the second table is
read with no grant, no join line and no row predicate (JOIN-GRANT-ZW, found by
the #408 adversary round).

What is refused is what the two tokenizers are measured to split differently,
plus what no SQL text needs:

* control characters other than tab, LF and CR (DuckDB truncates a statement
  at NUL and refuses the rest; sqlglot keeps them in an identifier);
* lone surrogates (no valid UTF-8 encodes one);
* the format characters DuckDB reads as whitespace: U+200B, U+2060, U+FEFF.

Other format characters (U+200C, U+200D in Persian, Indic and emoji text, the
soft hyphen, bidi marks) and unassigned or private-use code points are read
the same way by both tokenizers and occur in real data, so a literal holding
one is not refused: a control that refuses an answerable question is a
failure too. ``tests/test_execution/test_sql_codepoints_zw.py`` sweeps every
allowed code point through both tokenizers, so a sqlglot or DuckDB upgrade
that adds a disagreement fails CI instead of serving.

This module imports only the standard library: the plan+SQL package, the
insights gates, the SQL guardrail and the manifest enforcer all use it.
"""

from __future__ import annotations

import unicodedata

#: Unicode general categories refused anywhere in generated SQL.
REFUSED_CATEGORIES = frozenset({"Cc", "Cs"})
#: Format characters DuckDB reads as whitespace and sqlglot keeps in a name.
SPLIT_FORMAT = frozenset({"\u200b", "\u2060", "\ufeff"})
#: Control characters both parsers read as plain whitespace.
_ALLOWED_CONTROLS = frozenset("\t\n\r")


def hidden_codepoint(sql: str) -> str | None:
    """``U+XXXX`` for the first refused character in ``sql``, else None."""
    for ch in sql or "":
        if ch in _ALLOWED_CONTROLS:
            continue
        if ch in SPLIT_FORMAT or unicodedata.category(ch) in REFUSED_CATEGORIES:
            return f"U+{ord(ch):04X}"
    return None


def hidden_codepoint_reason(sql: str) -> str | None:
    """The refusal text for SQL carrying a refused character, else None."""
    found = hidden_codepoint(sql)
    if found is None:
        return None
    return (
        f"SQL carries {found}, an invisible or control character the SQL gate "
        "and DuckDB may split differently; refused"
    )


__all__ = ["REFUSED_CATEGORIES", "SPLIT_FORMAT", "hidden_codepoint", "hidden_codepoint_reason"]
