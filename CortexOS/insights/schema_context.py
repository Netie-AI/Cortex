"""Caller schema for the insights generate leg.

When ``schema_context`` is present it replaces the pack table list. The SQL
the model returns may name only tables and joins that string grants. A full
table read is refused from the parsed AST. Row results are capped. None of
this runs when the field is absent, so the pack path stays as it was.

The raw string is never logged. Length and sha256 only.
"""

from __future__ import annotations

import hashlib
import logging
import os
from collections.abc import Mapping, Sequence
from contextvars import ContextVar
from typing import Any

import sqlglot
from sqlglot import exp
from sqlglot.errors import SqlglotError

_log = logging.getLogger(__name__)

#: Character cap. DMS packs to about 1920 characters; over this is a named 422.
SCHEMA_CONTEXT_MAX_CHARS = 8192
SCHEMA_CONTEXT_TOO_LARGE = "schema_context_too_large"
SCHEMA_CONTEXT_INVALID = "schema_context_invalid"
BRUTE_FORCE_SCAN = "brute_force_scan"

#: Default result cap. Override with CORTEX_SCHEMA_CONTEXT_ROW_CAP.
SCHEMA_CONTEXT_ROW_CAP = 500
ROW_CAP_ENV = "CORTEX_SCHEMA_CONTEXT_ROW_CAP"

_USAGE_KEYS = ("prompt_tokens", "completion_tokens", "total_tokens")
_usage_slot: ContextVar[dict[str, int | None] | None] = ContextVar(
    "insights_schema_usage", default=None
)


def schema_idents(text: str) -> set[str]:
    """Identifiers in ``text``. Split on anything that is not a name character."""
    names: set[str] = set()
    buf: list[str] = []
    for ch in text.lower():
        if ch.isalnum() or ch == "_":
            buf.append(ch)
        elif buf:
            names.add("".join(buf))
            buf.clear()
    if buf:
        names.add("".join(buf))
    return names


def problem(value: Any) -> tuple[str, str] | None:
    """Named refusal for a bad ``schema_context``, or None when it may be used.

    ``None`` and a blank string are absent, not an error. Over-cap is never
    truncated.
    """
    if value is None:
        return None
    if not isinstance(value, str):
        return (SCHEMA_CONTEXT_INVALID, "schema_context must be a string")
    if len(value) > SCHEMA_CONTEXT_MAX_CHARS:
        return (
            SCHEMA_CONTEXT_TOO_LARGE,
            f"schema_context length {len(value)} exceeds {SCHEMA_CONTEXT_MAX_CHARS}",
        )
    return None


def accepted_text(value: Any) -> str | None:
    """Stripped schema to offer the model, or None when the field is absent."""
    if problem(value) is not None or not isinstance(value, str):
        return None
    text = value.strip()
    return text or None


def supplied_schema(value: str | None) -> str | None:
    """Schema already accepted by the route. Blank is absent."""
    if not isinstance(value, str):
        return None
    text = value.strip()
    return text or None


def log_schema_context(value: str) -> None:
    """INFO log of length and sha256. The raw string is not a log argument."""
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()
    _log.info("schema_context len=%s sha256=%s", len(value), digest)


def row_cap() -> int:
    """Result cap for a schema_context response. Env overrides the constant."""
    raw = os.environ.get(ROW_CAP_ENV, "").strip()
    if raw.isdigit():
        return int(raw)
    return SCHEMA_CONTEXT_ROW_CAP


def stamp_row_cap(envelope: dict[str, Any]) -> dict[str, Any]:
    """Slice ``values`` to the cap and stamp ``truncated`` / ``row_cap``."""
    cap = row_cap()
    out = dict(envelope)
    values = out.get("values")
    truncated = False
    if isinstance(values, list) and len(values) > cap:
        out["values"] = list(values[:cap])
        truncated = True
    out["row_cap"] = cap
    out["truncated"] = truncated
    return out


def _as_int(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return int(value)


def usage_from_calls(calls: Sequence[Mapping[str, Any]] | None) -> dict[str, int | None]:
    """Sum provider counts across attempts.

    A call that reports no integer counts is ignored. A field is null when no
    counted call reported it, or when one of them omitted it. Missing is never
    stored as 0. An explicit 0 is a real count.
    """
    rows: list[dict[str, int | None]] = []
    for call in calls or []:
        if not isinstance(call, Mapping):
            continue
        row = {key: _as_int(call.get(key)) for key in _USAGE_KEYS}
        if any(val is not None for val in row.values()):
            rows.append(row)
    out: dict[str, int | None] = {}
    for key in _USAGE_KEYS:
        vals = [row[key] for row in rows]
        if not vals or any(val is None for val in vals):
            out[key] = None
        else:
            out[key] = sum(vals)
    return out


def null_usage() -> dict[str, None]:
    return {key: None for key in _USAGE_KEYS}


def clear_usage() -> None:
    _usage_slot.set(None)


def publish_usage(usage: Mapping[str, Any]) -> None:
    _usage_slot.set({key: _as_int(usage.get(key)) for key in _USAGE_KEYS})


def consume_usage() -> dict[str, int | None]:
    current = _usage_slot.get()
    _usage_slot.set(None)
    if not isinstance(current, dict):
        return null_usage()
    return {key: _as_int(current.get(key)) for key in _USAGE_KEYS}


def _statements(sql: str) -> list[exp.Expression] | None:
    try:
        trees = sqlglot.parse(sql or "", read="duckdb")
    except SqlglotError:
        return None
    if not trees or any(tree is None for tree in trees):
        return None
    return [tree for tree in trees if tree is not None]


def _cte_names(tree: exp.Expression) -> set[str]:
    return {str(cte.alias_or_name or "").lower() for cte in tree.find_all(exp.CTE)} - {""}


def _iter_child_exprs(node: exp.Expression) -> list[exp.Expression]:
    children: list[exp.Expression] = []
    for value in node.args.values():
        if isinstance(value, exp.Expression):
            children.append(value)
        elif isinstance(value, list):
            children.extend(item for item in value if isinstance(item, exp.Expression))
    return children


def _local_tables(node: exp.Expression, ctes: set[str]) -> list[str]:
    """Table names under ``node``, not descending into a nested SELECT."""
    found: list[str] = []

    def rec(current: exp.Expression) -> None:
        if isinstance(current, exp.Select):
            return
        if isinstance(current, exp.Table):
            name = (current.name or "").lower()
            if name and name not in ctes:
                found.append(name)
            return
        for child in _iter_child_exprs(current):
            rec(child)

    rec(node)
    return found


def _own_tables(sel: exp.Select, ctes: set[str]) -> list[str]:
    found: list[str] = []
    frm = sel.args.get("from_")
    if isinstance(frm, exp.Expression):
        found.extend(_local_tables(frm, ctes))
    for join in sel.args.get("joins") or []:
        if isinstance(join, exp.Expression):
            found.extend(_local_tables(join, ctes))
    return found


def _expr_has_agg(node: Any) -> bool:
    if isinstance(node, exp.Select):
        return False
    if isinstance(node, exp.AggFunc):
        return True
    if isinstance(node, list):
        return any(_expr_has_agg(item) for item in node)
    if not isinstance(node, exp.Expression):
        return False
    return any(_expr_has_agg(child) for child in _iter_child_exprs(node))


def _select_bounded(sel: exp.Select) -> bool:
    """True when this SELECT has a filter, an aggregate, or a limit."""
    if sel.args.get("where") is not None:
        return True
    if sel.args.get("having") is not None:
        return True
    if sel.args.get("limit") is not None:
        return True
    if sel.args.get("group") is not None:
        return True
    return any(_expr_has_agg(expr) for expr in (sel.args.get("expressions") or []))


def _rel_name(node: exp.Expression | None) -> str:
    if isinstance(node, exp.Table):
        return (node.name or "").lower()
    if isinstance(node, exp.Alias) and isinstance(node.this, exp.Expression):
        return _rel_name(node.this)
    return ""


def _physical_tables(tree: exp.Expression) -> list[str]:
    ctes = _cte_names(tree)
    names: list[str] = []
    for table in tree.find_all(exp.Table):
        name = (table.name or "").lower()
        if name and name not in ctes:
            names.append(name)
    return names


def _join_pairs(tree: exp.Expression) -> list[tuple[str, str]]:
    ctes = _cte_names(tree)
    pairs: list[tuple[str, str]] = []
    for sel in tree.find_all(exp.Select):
        frm = sel.args.get("from_")
        left = _rel_name(frm.this) if isinstance(frm, exp.Expression) else ""
        for join in sel.args.get("joins") or []:
            if not isinstance(join, exp.Expression):
                continue
            right_node = join.this if isinstance(join.this, exp.Expression) else None
            right = _rel_name(right_node)
            if left and right and left not in ctes and right not in ctes and left != right:
                pairs.append(tuple(sorted((left, right))))
            if right:
                left = right
    return pairs


def _join_granted(left: str, right: str, schema: str) -> bool:
    need = {left, right}
    for line in schema.splitlines():
        if need <= schema_idents(line):
            return True
    return False


def ungranted_reason(sql: str, schema: str) -> str | None:
    """Named refusal when SQL names a table or join the schema did not grant.

    Parse failure returns None so the existing SQL check can name it. The
    reason names the table or the join pair and does not include the SQL.
    """
    trees = _statements(sql)
    if trees is None:
        return None
    granted = schema_idents(schema)
    tables: list[str] = []
    pairs: list[tuple[str, str]] = []
    for tree in trees:
        tables.extend(_physical_tables(tree))
        pairs.extend(_join_pairs(tree))
    for name in sorted(set(tables)):
        if name not in granted:
            return f"schema_context:ungranted_table:{name}"
    for left, right in sorted(set(pairs)):
        if not _join_granted(left, right, schema):
            return f"schema_context:ungranted_join:{left}+{right}"
    return None


def brute_force_reason(sql: str) -> str | None:
    """``brute_force_scan`` when a SELECT reads a table with no filter, aggregate, or limit.

    Decided from the AST. Parse failure returns None.
    """
    trees = _statements(sql)
    if trees is None:
        return None
    for tree in trees:
        ctes = _cte_names(tree)
        for sel in tree.find_all(exp.Select):
            if not isinstance(sel, exp.Select):
                continue
            if _select_bounded(sel):
                continue
            if _own_tables(sel, ctes):
                return BRUTE_FORCE_SCAN
    return None
