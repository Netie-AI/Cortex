"""Which table references are CTE references -- decided by scope, not by name.

Every CTE bypass found so far had one root cause: a hand-rolled "is this name
a CTE" check that collected ``WITH`` names from anywhere in the tree and then
exempted any bare table of that name, anywhere in the tree. SQL does not bind
that way:

* a CTE declared inside a derived table is invisible outside it, so
  ``FROM (WITH secrets AS (...) SELECT ...) d, secrets`` reads the real
  ``secrets``;
* a CTE body sees only the CTEs *before* it in the same ``WITH`` list, so in
  ``WITH a AS (SELECT * FROM secrets), secrets AS (...)`` DuckDB binds the first
  ``secrets`` to the base table;
* a recursive CTE sees itself only in its recursive arm; the anchor arm of
  ``WITH RECURSIVE secrets AS (SELECT .. FROM secrets UNION ALL ..)`` reads the
  base table.

This module asks sqlglot's scope builder instead (``traverse_scope``), which
models ``WITH`` ordering, nesting and derived tables, and treats a reference as
a CTE only when the scope that holds it resolves the name to a CTE. Every other
``exp.Table`` is a real table and the caller must check it. Anything the scope
builder cannot analyse is a named refusal (:class:`ScopeError`), never a guess.

It never executes anything and never imports a pack.
"""

from __future__ import annotations

from sqlglot import exp
from sqlglot.optimizer.scope import Scope, traverse_scope


class ScopeError(ValueError):
    """The statement's name binding could not be proven; the caller refuses."""


def dotted_part(table: exp.Table) -> str | None:
    """The first identifier part of ``table`` that itself contains a dot.

    ``main."bronze.schools"`` is the table literally named ``bronze.schools`` in
    ``main``, not ``bronze.schools``. Joining parts into one string and
    comparing it with a grant key conflates the two, so a part with a dot in it
    is never matched against anything.
    """
    for key in ("catalog", "db", "this"):
        part = table.args.get(key)
        if isinstance(part, exp.Identifier) and "." in part.name:
            return part.name
    return None


def _is_recursive_arm(cte: exp.CTE, scope: Scope) -> bool:
    """Whether ``scope`` is the recursive arm of the recursive CTE ``cte``.

    Only there does a CTE's own name bind to itself. The anchor arm, a nested
    subquery, and every non-recursive CTE body bind the name to whatever is in
    scope outside the CTE -- in DuckDB, the base table.
    """
    with_ = cte.parent
    if not isinstance(with_, exp.With) or not with_.args.get("recursive"):
        return False
    body = cte.this
    return isinstance(body, exp.Union) and scope.expression is body.expression


def _binds_to_cte(table: exp.Table, name: str, scope: Scope) -> bool:
    node = table.parent
    while node is not None:
        if isinstance(node, exp.CTE) and node.alias == name:
            # A reference to a CTE from inside that CTE's own body.
            return _is_recursive_arm(node, scope)
        node = node.parent
    return True


def cte_reference_ids(root: exp.Expression, *, allow_recursive: bool) -> set[int]:
    """``id()`` of every ``exp.Table`` in ``root`` that scope resolves to a CTE.

    A table is a CTE reference only when it is unqualified, the scope holding it
    has that exact name among its visible CTEs, and it is not a
    self-reference outside a recursive arm. Everything else -- including any
    table the scope builder never visited -- is a real table.

    ``allow_recursive=False`` refuses ``WITH RECURSIVE`` outright. Raises
    :class:`ScopeError` when the scope cannot be built.
    """
    if not allow_recursive:
        for with_ in root.find_all(exp.With):
            if with_.args.get("recursive"):
                raise ScopeError("recursive CTE refused: WITH RECURSIVE is not analysed here")
    try:
        scopes = traverse_scope(root)
    except RecursionError as exc:
        raise ScopeError("SQL nests too deeply to resolve its scopes") from exc
    except Exception as exc:  # noqa: BLE001 - any scope failure is a refusal
        raise ScopeError(f"scope analysis failed ({type(exc).__name__})") from exc
    if not scopes and root.find(exp.Table) is not None:
        raise ScopeError(f"no scope could be built for {type(root).__name__.upper()}")

    ids: set[int] = set()
    for scope in scopes:
        for table in scope.tables:
            if not isinstance(table.this, exp.Identifier):
                continue
            if table.text("db") or table.text("catalog"):
                continue  # a qualified name always reaches the base table
            name = table.name
            if not name or name not in scope.cte_sources:
                continue
            if _binds_to_cte(table, name, scope):
                ids.add(id(table))
    return ids


__all__ = ["ScopeError", "cte_reference_ids", "dotted_part"]
