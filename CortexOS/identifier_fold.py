"""Fold a SQL identifier the way DuckDB compares catalog names. Pure string code.

A grant check that compares names under a different rule from the one DuckDB
uses to resolve them is a bypass: the check answers "granted" for one relation
and DuckDB reads another. DuckDB (checked empirically on 1.5) resolves table,
schema, column and CTE names with *ASCII-only* case-insensitive comparison, and
quoting does not change that:

* ``ORDERS``, ``"ORDERS"`` and ``"Orders"`` all reach the table ``orders``;
* ``"Äpfel"`` and ``"äpfel"`` are two different tables, while ``ÄPFEL`` reaches
  ``"Äpfel"`` (only the ASCII letters fold);
* the Kelvin sign (U+212A) is not ``K``: a table spelled with it followed by
  ``elvin`` is a different table from ``kelvin``.

Python's ``str.lower()`` and ``str.casefold()`` are Unicode-aware and fold all
three of those non-ASCII cases together, so they must never be used to compare
a referenced name with a granted one. Every grant matcher folds both sides with
:func:`fold_identifier` instead.

This module has no imports on purpose: the plan+SQL package, which may not
import the executor or a database driver, uses it too.
"""

from __future__ import annotations

_ASCII_UPPER_TO_LOWER = str.maketrans("ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz")


def fold_identifier(name: str) -> str:
    """``name`` with ASCII ``A``-``Z`` lowered and every other character kept.

    Two identifiers name the same DuckDB relation or column exactly when their
    folds are equal. Whitespace is not stripped: ``" orders"`` is a different
    quoted identifier from ``orders``.
    """
    return name.translate(_ASCII_UPPER_TO_LOWER)


__all__ = ["fold_identifier"]
