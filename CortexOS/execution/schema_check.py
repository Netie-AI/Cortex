"""Generated-SQL columns vs the executing warehouse's real schema.

The static SQL checks upstream (``sql_guardrail``, the crew ``validate_sql``)
compare model SQL against the *ontology's* column list. When the ontology
drifts from the warehouse that actually runs the query, that check certifies
SQL the warehouse's binder then rejects (live: ``transactions.timestamp``,
``shipments.supplier_id``, ``locations.location_name`` against a warehouse
that has ``ts`` / no ``supplier_id`` / ``name``). This module closes that gap
by resolving every column reference against ``information_schema`` of the
serving warehouse.

It never runs the candidate SQL — not even ``EXPLAIN``, which would bind table
functions such as ``read_csv`` and so touch the filesystem. DuckDB here only
reads the catalogue, through the read-only cursor ``warehouse`` owns.

This is an additional refusal, never a relaxation: it does not replace or
narrow ``manifest.enforce_manifest``.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from CortexOS.execution.warehouse import _read_only_cursor, warehouse_path

#: Violation prefix a caller can match on (and a customer can read).
UNKNOWN_COLUMN = "UNKNOWN_WAREHOUSE_COLUMN"
UNKNOWN_TABLE = "UNKNOWN_WAREHOUSE_TABLE"
UNRESOLVED = "UNRESOLVED_WAREHOUSE_COLUMN"

WarehouseSchema = Mapping[str, frozenset[str]]


@dataclass(frozen=True, slots=True)
class SchemaCheck:
    """``ok`` is only meaningful when ``checked``; unchecked means no schema."""

    ok: bool
    checked: bool
    violations: list[str] = field(default_factory=list)
    detail: str = ""


def warehouse_schema(db_path: Path | str | None = None) -> dict[str, frozenset[str]] | None:
    """``table -> columns`` (lower-cased) of the serving warehouse's ``main`` schema.

    ``None`` when there is no warehouse file or it cannot be opened read-only.
    Never opens read-write: a schema lookup must not take the writer lock.
    """
    path = warehouse_path(db_path)
    if not path.is_file():
        return None
    cur = _read_only_cursor(path)
    if cur is None:
        return None
    try:
        rows = cur.execute(
            "SELECT table_name, column_name FROM information_schema.columns "
            "WHERE table_schema = 'main'"
        ).fetchall()
    except Exception:  # noqa: BLE001 - an unreadable catalogue is "no schema"
        return None
    finally:
        try:
            cur.close()
        except Exception:  # noqa: BLE001
            pass
    out: dict[str, set[str]] = {}
    for table, column in rows:
        out.setdefault(str(table).lower(), set()).add(str(column).lower())
    return {t: frozenset(c) for t, c in out.items()}


def _named_violations(stmt, schema: WarehouseSchema) -> list[str]:  # noqa: ANN001
    """table.column names for references that do not exist. Best effort naming."""
    from sqlglot import exp
    from sqlglot.optimizer.scope import traverse_scope

    found: list[str] = []
    for scope in traverse_scope(stmt):
        # A projection alias may be referenced by ORDER BY / HAVING. `x AS x`
        # is not an alias of anything new, so it never excuses `x`.
        aliases = {
            str(e.alias).lower()
            for e in getattr(scope.expression, "expressions", []) or []
            if isinstance(e, exp.Alias)
            and not (isinstance(e.this, exp.Column) and e.this.name.lower() == str(e.alias).lower())
        }
        base = {
            name: src
            for name, src in scope.sources.items()
            if isinstance(src, exp.Table)
        }
        for col in scope.columns:
            name = col.name.lower()
            if not name or name == "*":
                continue
            qual = col.table
            if qual:
                src = base.get(qual)
                if src is None:
                    continue  # derived table / CTE: resolved by its own scope
                real = src.name.lower()
                if real not in schema:
                    found.append(f"{UNKNOWN_TABLE}:{real}")
                elif name not in schema[real]:
                    found.append(f"{UNKNOWN_COLUMN}:{real}.{name}")
                continue
            if name in aliases:
                continue
            if len(scope.sources) == 1 and base:
                real = next(iter(base.values())).name.lower()
                if real not in schema:
                    found.append(f"{UNKNOWN_TABLE}:{real}")
                elif name not in schema[real]:
                    found.append(f"{UNKNOWN_COLUMN}:{real}.{name}")
                continue
            owners = [s.name.lower() for s in base.values() if name in schema.get(s.name.lower(), ())]
            if not owners and len(base) == len(scope.sources):
                tables = ",".join(sorted({s.name.lower() for s in base.values()}))
                found.append(f"{UNKNOWN_COLUMN}:{{{tables}}}.{name}")
    return list(dict.fromkeys(found))


def check_columns(sql: str, schema: WarehouseSchema | None) -> SchemaCheck:
    """Resolve every column in ``sql`` against ``schema``. Fail closed on doubt.

    ``schema=None`` returns ``checked=False`` (nothing to check against); the
    caller decides what an unchecked result means. Any column sqlglot cannot
    resolve against the real tables is a violation — a query the check cannot
    fully resolve is one it cannot prove the warehouse will bind.
    """
    if schema is None:
        return SchemaCheck(ok=True, checked=False, detail="no executing warehouse schema")
    import sqlglot
    from sqlglot.errors import OptimizeError
    from sqlglot.optimizer.qualify import qualify

    try:
        stmt = sqlglot.parse_one(sql, read="duckdb")
    except Exception as exc:  # noqa: BLE001
        return SchemaCheck(ok=False, checked=True, violations=[f"PARSE_ERROR:{exc}"[:200]])
    typed = {t: {c: "UNKNOWN" for c in cols} for t, cols in schema.items()}
    try:
        qualify(
            stmt.copy(),
            schema=typed,
            dialect="duckdb",
            validate_qualify_columns=True,
            quote_identifiers=False,
        )
        return SchemaCheck(ok=True, checked=True, detail="columns resolved against warehouse")
    except OptimizeError as exc:
        detail = str(exc)[:200]
    except Exception as exc:  # noqa: BLE001 - unanalysable is not provably bindable
        detail = f"{type(exc).__name__}: {exc}"[:200]
    try:
        # Name from the unqualified parse: qualify raises on the very columns
        # we want to name, even with validation off.
        named = _named_violations(stmt, schema)
    except Exception:  # noqa: BLE001
        named = []
    return SchemaCheck(
        ok=False,
        checked=True,
        violations=named or [f"{UNRESOLVED}:{detail}"],
        detail=detail,
    )


def check_generated_sql(sql: str, db_path: Path | str | None = None) -> SchemaCheck:
    """``check_columns`` against the serving warehouse ``warehouse_path`` resolves."""
    return check_columns(sql, warehouse_schema(db_path))


__all__ = [
    "UNKNOWN_COLUMN",
    "UNKNOWN_TABLE",
    "UNRESOLVED",
    "SchemaCheck",
    "check_columns",
    "check_generated_sql",
    "warehouse_schema",
]
