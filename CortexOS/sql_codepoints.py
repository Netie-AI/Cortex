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

The class is refused, not the three characters: any control character other
than tab, LF and CR, any format character (Cf: zero-width marks, joiners, bidi
controls, tags), line and paragraph separators, surrogates, private-use and
unassigned code points. None of them belongs in generated SQL, inside a string
literal or out, and each is a place where two tokenizers may disagree.
``tests/test_execution/test_sql_codepoints_zw.py`` sweeps every code point
through both parsers so a new disagreement fails CI instead of serving.

This module imports only the standard library: the plan+SQL package, the
insights gates and the manifest enforcer all use it.
"""

from __future__ import annotations

import unicodedata

#: Unicode general categories refused anywhere in generated SQL.
REFUSED_CATEGORIES = frozenset({"Cc", "Cf", "Zl", "Zp", "Cs", "Co", "Cn"})
#: Control characters both parsers read as plain whitespace.
_ALLOWED_CONTROLS = frozenset("\t\n\r")


def hidden_codepoint(sql: str) -> str | None:
    """``U+XXXX`` for the first refused character in ``sql``, else None."""
    for ch in sql or "":
        if ch in _ALLOWED_CONTROLS:
            continue
        if unicodedata.category(ch) in REFUSED_CATEGORIES:
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


__all__ = ["REFUSED_CATEGORIES", "hidden_codepoint", "hidden_codepoint_reason"]
